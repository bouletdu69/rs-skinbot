import os
import requests
import uuid
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .database import engine, Base, get_db, SessionLocal
from .models import SkinUpload
from .builder import validate_and_extract_preview, build_pack_task, load_packs, save_packs, extract_to_acsm, load_settings, save_settings
from apscheduler.schedulers.background import BackgroundScheduler

# Create tables
Base.metadata.create_all(bind=engine)

def auto_build_job():
    print("Running hourly auto-build...")
    db = SessionLocal()
    try:
        skins = db.query(SkinUpload).filter(SkinUpload.status == "uploaded").all()
        if skins:
            pack_names = set(skin.pack_name for skin in skins)
            for pack_name in pack_names:
                print(f"Starting auto-build for pack: {pack_name}")
                build_pack_task(pack_name)
        else:
            print("No pending skins found. Skipping auto-build.")
    except Exception as e:
        print(f"Auto-build job error: {e}")
    finally:
        db.close()

scheduler = BackgroundScheduler()
# On lance la tâche à chaque heure pile (minute=0)
scheduler.add_job(auto_build_job, 'cron', minute=0)
scheduler.start()

app = FastAPI(title="AC Skinpack Manager API")

# Mount previews directory so the bot can fetch images
app.mount("/previews", StaticFiles(directory="/app/previews"), name="previews")

API_TOKEN = os.getenv("API_TOKEN", "default_insecure_token")

def verify_token(token: str):
    if token != API_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid API Token")
    return token

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/upload")
async def upload_skin(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    discord_user_id: str = Form(...),
    discord_username: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Endpoint for users to upload their skins.
    Validates the skin and returns a preview URL if successful.
    """
    if not file.filename.endswith(('.zip', '.7z', '.rar')):
         raise HTTPException(status_code=400, detail="Seuls les fichiers .zip, .7z, .rar sont autorisés.")
    
    upload_id = str(uuid.uuid4())
    internal_filename = f"{upload_id}_{file.filename}"
    save_path = f"/app/tmp_builds/{internal_filename}"
    
    with open(save_path, "wb") as buffer:
        while content := await file.read(1024 * 1024):  # Read in chunks of 1MB
            buffer.write(content)
            
    # Validate the uploaded zip
    is_valid, msg, preview_filename, car_dir_name = validate_and_extract_preview(save_path, upload_id)
    
    if not is_valid:
        os.remove(save_path) # Clean up invalid file
        raise HTTPException(status_code=400, detail=msg)

    # Find which pack this car belongs to
    matched_packs = []
    for p_name, cars in load_packs().items():
        if car_dir_name in cars:
            matched_packs.append(p_name)
    
    if not matched_packs:
        os.remove(save_path)
        raise HTTPException(status_code=400, detail=f"La voiture '{car_dir_name}' n'est autorisée dans aucun championnat actuel")

    # Save to database
    is_multiple = len(matched_packs) > 1
    new_upload = SkinUpload(
        id=upload_id,
        discord_user_id=discord_user_id,
        discord_username=discord_username,
        original_filename=file.filename,
        internal_filename=internal_filename,
        pack_name=matched_packs[0] if not is_multiple else None,
        status="uploaded" if not is_multiple else "pending_selection"
    )
    db.add(new_upload)
    db.commit()
    
    preview_url = f"/previews/{preview_filename}" if preview_filename else None

    # Notify the bot
    try:
        if is_multiple:
            requests.post("http://bot:8080/notify_selection", json={
                "upload_id": upload_id,
                "discord_user_id": discord_user_id,
                "username": discord_username,
                "matched_packs": matched_packs
            }, timeout=5)
            return {
                "status": "pending_selection", 
                "upload_id": upload_id, 
                "message": "Votre voiture est inscrite dans plusieurs championnats. Vérifiez vos messages Discord pour sélectionner le championnat !"
            }
        else:
            requests.post("http://bot:8080/notify_preview", json={
                "upload_id": upload_id,
                "preview_url": preview_url,
                "username": discord_username,
                "pack_name": matched_packs[0]
            }, timeout=5)
    except Exception as e:
        print(f"Failed to notify bot: {e}")
        
    settings = load_settings()
    upload_mode = settings.get("upload_mode", "direct")
    
    if not is_multiple:
        # Extract to ACSM if mode permits
        acsm_dir = os.getenv("ACSM_CARS_DIR")
        if acsm_dir and upload_mode in ["direct", "both"]:
            background_tasks.add_task(extract_to_acsm, save_path, acsm_dir)

        # Build pack if mode permits
        if upload_mode in ["pack_only", "both"]:
            background_tasks.add_task(build_pack_task, matched_packs[0])

    return {
        "status": "success", 
        "upload_id": upload_id, 
        "filename": file.filename,
        "message": msg,
        "preview_url": preview_url
    }

@app.post("/upload/{upload_id}/select_pack")
def select_pack(
    upload_id: str,
    selected_pack: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    token: str = Depends(verify_token)
):
    upload = db.query(SkinUpload).filter(SkinUpload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload introuvable")
        
    if upload.status != "pending_selection":
        raise HTTPException(status_code=400, detail="Cet upload n'est pas en attente de sélection")

    upload.pack_name = selected_pack
    upload.status = "uploaded"
    db.commit()

    settings = load_settings()
    upload_mode = settings.get("upload_mode", "direct")
    save_path = f"/app/tmp_builds/{upload.internal_filename}"

    # Extract to ACSM if mode permits
    acsm_dir = os.getenv("ACSM_CARS_DIR")
    if acsm_dir and upload_mode in ["direct", "both"]:
        background_tasks.add_task(extract_to_acsm, save_path, acsm_dir)

    # Build pack if mode permits
    if upload_mode in ["pack_only", "both"]:
        background_tasks.add_task(build_pack_task, selected_pack)

    # Notify bot for preview
    preview_filename = f"{upload_id}.jpg" if os.path.exists(f"/app/previews/{upload_id}.jpg") else None
    try:
        requests.post("http://bot:8080/notify_preview", json={
            "upload_id": upload_id,
            "preview_url": f"/previews/{preview_filename}" if preview_filename else None,
            "username": upload.discord_username,
            "pack_name": selected_pack
        }, timeout=5)
    except Exception as e:
        print(f"Failed to notify bot: {e}")

    return {"status": "success", "pack_name": selected_pack}

@app.post("/build")
def trigger_build(pack_name: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db), token: str = Depends(verify_token)):
    """
    Endpoint triggered by the Bot to compile the skinpack.
    Runs the heavy zipping task in the background.
    """
    skins = db.query(SkinUpload).filter(SkinUpload.pack_name == pack_name, SkinUpload.status == "uploaded").all()
    if not skins:
         raise HTTPException(status_code=400, detail=f"No pending skins to compile for '{pack_name}'. You must upload a skin first.")

    background_tasks.add_task(build_pack_task, pack_name)
    return {"status": "building", "pack_name": pack_name}

@app.post("/build_all")
def build_all_packs(background_tasks: BackgroundTasks, db: Session = Depends(get_db), token: str = Depends(verify_token)):
    """
    Endpoint triggered by the Bot to compile all skinpacks.
    Checks all packs, compiles the ones with pending skins, and returns a summary.
    """
    packs = load_packs()
    updated = []
    unchanged = []
    
    for pack_name in packs.keys():
        skins = db.query(SkinUpload).filter(SkinUpload.pack_name == pack_name, SkinUpload.status == "uploaded").all()
        if skins:
            updated.append(pack_name)
            background_tasks.add_task(build_pack_task, pack_name)
        else:
            unchanged.append(pack_name)
            
    return {"updated": updated, "unchanged": unchanged}

@app.get("/status/{pack_name}")
def get_pack_status(pack_name: str, db: Session = Depends(get_db), token: str = Depends(verify_token)):
    """
    Endpoint for the bot to check if the pack is done building.
    """
    skins = db.query(SkinUpload).filter(SkinUpload.pack_name == pack_name).all()
    if not skins:
         raise HTTPException(status_code=404, detail="Pack not found")
    
    total = len(skins)
    packed = len([s for s in skins if s.status == "packed"])
    status = "ready" if total == packed and total > 0 else "processing"
    
    return {"pack_name": pack_name, "total_skins": total, "status": status}

@app.get("/settings")
def get_settings(token: str = Depends(verify_token)):
    return load_settings()

@app.post("/settings")
async def update_settings(request: Request, token: str = Depends(verify_token)):
    data = await request.json()
    settings = load_settings()
    settings.update(data)
    save_settings(settings)
    return {"status": "success", "settings": settings}

@app.get("/packs")
def get_packs(token: str = Depends(verify_token)):
    return load_packs()

@app.post("/packs/{pack_name}")
def create_pack(pack_name: str, token: str = Depends(verify_token)):
    packs = load_packs()
    if pack_name in packs:
        raise HTTPException(status_code=400, detail="Pack already exists")
    packs[pack_name] = []
    save_packs(packs)
    return {"status": "success", "pack_name": pack_name, "cars": []}

@app.delete("/packs/{pack_name}")
def delete_pack(pack_name: str, token: str = Depends(verify_token)):
    packs = load_packs()
    if pack_name not in packs:
        raise HTTPException(status_code=404, detail="Pack not found")
    del packs[pack_name]
    save_packs(packs)
    return {"status": "success", "deleted": pack_name}

@app.post("/packs/{pack_name}/cars/{car_name}")
def add_car_to_pack(pack_name: str, car_name: str, token: str = Depends(verify_token)):
    packs = load_packs()
    if pack_name not in packs:
        raise HTTPException(status_code=404, detail="Pack not found")
    if car_name in packs[pack_name]:
        raise HTTPException(status_code=400, detail="Car already in pack")
    

    packs[pack_name].append(car_name)
    save_packs(packs)
    return {"status": "success", "pack": pack_name, "car_added": car_name}

@app.delete("/packs/{pack_name}/cars/{car_name}")
def remove_car_from_pack(pack_name: str, car_name: str, token: str = Depends(verify_token)):
    packs = load_packs()
    if pack_name not in packs:
        raise HTTPException(status_code=404, detail="Pack not found")
    if car_name not in packs[pack_name]:
        raise HTTPException(status_code=404, detail="Car not in pack")
    
    packs[pack_name].remove(car_name)
    save_packs(packs)
    return {"status": "success", "pack": pack_name, "car_removed": car_name}
