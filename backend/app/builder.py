import os
import zipfile
import tempfile
import shutil
import subprocess
from pathlib import Path
from typing import Tuple, Optional
from sqlalchemy.orm import Session
import json
import requests
from .models import SkinUpload
from .database import SessionLocal

PREVIEWS_DIR = Path("/app/previews")
PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
TMP_BUILDS_DIR = Path("/app/tmp_builds")
TMP_BUILDS_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_PACKS_DIR = Path("/app/public_packs")
PUBLIC_PACKS_DIR.mkdir(parents=True, exist_ok=True)

def load_packs():
    config_path = "/app/config/packs.json"
    if not os.path.exists(config_path):
        config_path = "config/packs.json"
    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_packs(packs_dict):
    config_path = "/app/config/packs.json"
    if not os.path.exists("/app/config"):
        config_path = "config/packs.json"
    with open(config_path, "w") as f:
        json.dump(packs_dict, f, indent=2)

def load_settings():
    config_path = "/app/config/settings.json"
    if not os.path.exists(config_path):
        config_path = "config/settings.json"
    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except Exception:
        return {"upload_mode": "direct"}

def save_settings(settings_dict):
    config_path = "/app/config/settings.json"
    if not os.path.exists("/app/config"):
        config_path = "config/settings.json"
    with open(config_path, "w") as f:
        json.dump(settings_dict, f, indent=2)



def list_archive_files(archive_path: str):
    result = subprocess.run(["7z", "l", "-slt", archive_path], capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        return None
    
    paths = []
    for line in result.stdout.splitlines():
        if line.startswith("Path = "):
            paths.append(line[7:].strip())
    return paths

def validate_and_extract_preview(zip_path: str, upload_id: str) -> Tuple[bool, str, Optional[str], Optional[str]]:
    """
    Checks if a zip/rar/7z contains a valid AC skin (ui_skin.json) and extracts the preview image.
    Returns (is_valid, message, preview_filename, pack_name)
    """
    file_list = list_archive_files(zip_path)
    if file_list is None:
        return False, "The file is not a valid archive (ZIP, RAR or 7Z).", None, None

    has_ui_skin = False
    detected_car_name = None
    preview_file_in_zip = None

    try:
        for f in file_list:
            f_norm = f.replace('\\', '/')
            if "__macosx" in f_norm.lower() or f_norm.split('/')[-1].startswith('._'):
                continue

            if "content/cars/" in f_norm.lower():
                parts = f_norm.split('/')
                try:
                    cars_idx = parts.index("cars")
                    if len(parts) > cars_idx + 1:
                        detected_car_name = parts[cars_idx + 1]
                except ValueError:
                    pass

            if f_norm.lower().endswith("ui_skin.json"):
                has_ui_skin = True
            if "preview." in f_norm.lower() and f_norm.lower().endswith(('.jpg', '.png', '.jpeg')):
                preview_file_in_zip = f

        if not detected_car_name:
            return False, "The archive must contain the correct directory structure (e.g. content/cars/car_name/...)", None, None
            
        if not has_ui_skin:
            return False, "ui_skin.json file not found in the archive. This is not a valid skin.", None, None
        


        if preview_file_in_zip:
            ext = Path(preview_file_in_zip).suffix
            preview_filename = f"{upload_id}{ext}"
            preview_dest = PREVIEWS_DIR / preview_filename
            
            with tempfile.TemporaryDirectory() as tmp_extract:
                subprocess.run(["7z", "e", zip_path, preview_file_in_zip, f"-o{tmp_extract}", "-y"], capture_output=True)
                extracted_file = Path(tmp_extract) / Path(preview_file_in_zip).name
                if extracted_file.exists():
                    shutil.move(str(extracted_file), str(preview_dest))
            
            return True, "Valid skin.", preview_filename, detected_car_name
        else:
            return True, "Valid skin (but no preview found).", None, detected_car_name
    except Exception as e:
        return False, f"Error reading the archive: {str(e)}", None, None

def build_pack_task(pack_name: str, notify: bool = True):
    """
    Background task to extract all skins for a pack and zip them into a final archive atomically.
    """
    tmp_pack_path = TMP_BUILDS_DIR / f"{pack_name}.tmp.zip"
    final_pack_path = PUBLIC_PACKS_DIR / f"{pack_name}.zip"

    db: Session = SessionLocal()
    try:
        new_skins = db.query(SkinUpload).filter(SkinUpload.pack_name == pack_name, SkinUpload.status == "uploaded").all()
        all_skins = db.query(SkinUpload).filter(SkinUpload.pack_name == pack_name).all()
        if not new_skins:
            return

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            
            for skin in all_skins:
                skin_path = TMP_BUILDS_DIR / skin.internal_filename
                if skin_path.exists():
                    subprocess.run(["7z", "x", str(skin_path), f"-o{temp_dir_path}", "-y"], capture_output=True)
            
            with zipfile.ZipFile(tmp_pack_path, 'w', zipfile.ZIP_DEFLATED) as z:
                for root, dirs, files in os.walk(temp_dir_path):
                    if "__MACOSX" in root.upper():
                        continue
                    for file in files:
                        if file.startswith("._"):
                            continue
                        file_path = Path(root) / file
                        arcname = file_path.relative_to(temp_dir_path)
                        z.write(file_path, arcname)

        shutil.move(str(tmp_pack_path), str(final_pack_path))
        
        for skin in new_skins:
            skin.status = "packed"
        db.commit()
        
        if notify:
            # Notify the bot that the pack is ready
            try:
                requests.post("http://bot:8080/notify_build", json={
                    "pack_name": pack_name
                }, timeout=5)
            except Exception as e:
                print(f"Failed to notify bot of manual build completion: {e}")
            
    except Exception as e:
        print(f"Error building pack {pack_name}: {e}")
    finally:
        db.close()

def extract_to_acsm(zip_path: str, acsm_cars_dir: str):
    """
    Extracts the content/cars directory from the zip archive directly to the ACSM content/cars folder.
    """
    try:
        acsm_path = Path(acsm_cars_dir)
        if not acsm_path.exists():
            print(f"ACSM directory not found: {acsm_cars_dir}")
            return
            
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            subprocess.run(["7z", "x", zip_path, f"-o{temp_dir_path}", "-y"], capture_output=True)
            
            for root, dirs, files in os.walk(temp_dir_path, topdown=False):
                for name in files:
                    if name.startswith("._") or name == ".DS_Store":
                        try:
                            os.remove(os.path.join(root, name))
                        except Exception:
                            pass
                for name in dirs:
                    if name.upper() == "__MACOSX" or name.startswith("._"):
                        try:
                            shutil.rmtree(os.path.join(root, name))
                        except Exception:
                            pass
            
            cars_dir = None
            for root, dirs, files in os.walk(temp_dir_path):
                if root.replace('\\', '/').lower().endswith('content/cars'):
                    cars_dir = Path(root)
                    break
            
            if cars_dir and cars_dir.exists():
                shutil.copytree(str(cars_dir), str(acsm_path), dirs_exist_ok=True)
                print(f"Successfully synced {zip_path} to ACSM.")
            else:
                print(f"No 'content/cars' structure found in {zip_path} for ACSM sync.")
                
    except Exception as e:
        print(f"Error syncing to ACSM: {e}")
