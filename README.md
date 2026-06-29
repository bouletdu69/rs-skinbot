# AC Skinpack Manager (Revival Skinbot)

A complete automated solution for managing, validating, and compiling Assetto Corsa skinpacks via a Discord bot and a web interface.

## 🌟 Features

* **Web Upload Interface:** Users can upload their `.zip`, `.7z`, or `.rar` skins through a simple web page linked directly to their Discord account.
* **Automatic Validation:** The backend automatically verifies the archive's structure (checks for `ui_skin.json` and the correct `content/cars/<car_name>` folder).
* **Discord Notifications:** The bot sends a message in a specific Discord channel with the extracted `preview.jpg` whenever a valid skin is uploaded.
* **Dynamic Pack Management:** Admins can create and manage championships directly from Discord using slash commands (no need to edit JSON files manually).
* **Multi-Championship Support:** A single car can be registered in multiple championships. When a player uploads a skin for such a car, the bot will prompt them via interactive Discord buttons to select which championship the skin belongs to.
* **Automated Compilation:** With a simple command, the bot aggregates all uploaded skins for a specific championship into a single, ready-to-use `.zip` file for players to download via Content Manager.
* **Smart Zipping:** The compilation process is smart enough to include all historical skins for a pack, preventing lost uploads and ensuring players always download the complete pack.
* **ACSM Integration:** Automatically extracts uploaded skins directly to the Assetto Corsa Server Manager (ACSM) cars directory so they are instantly playable on your server.
* **Configurable Upload Modes:** Server admins can configure what happens automatically when a new skin is uploaded (`direct` ACSM upload, `pack_only` zip build, `both`, or `manual`).

## 🏗️ Architecture

The project is fully containerized using Docker and is split into 4 microservices:
1. **Backend (FastAPI):** Handles file uploads, archive extraction (via `7z`), database interactions, and the heavy lifting of zipping the final packs.
2. **Discord Bot (discord.py):** Listens to slash commands, communicates with the backend, and posts notifications to users.
3. **Database (PostgreSQL):** Keeps track of uploaded skins, user IDs, and compilation statuses.
4. **Nginx:** Serves the frontend web interface, proxy-passes API requests to the backend, and serves the compiled `.zip` skinpacks to the public.

## 🚀 Prerequisites

* **Docker** and **Docker Compose** installed on your host machine.
* A registered **Discord Bot Application** with a valid token.
* A server/VPS to host the containers (or run it locally).

## ⚙️ Setup & Configuration

1. **Clone the repository:**
   ```bash
   git clone <your-repo-url>
   cd rs-skinbot
   ```

2. **Configure the Environment:**
   Create a `.env` file in the root directory of the project and fill in the required variables:
   ```env
   # Discord Bot Configuration
   DISCORD_TOKEN=your_discord_bot_token_here
   
   # ID of the specific Discord channel where the bot will listen and send notifications
   SKIN_CHANNEL_ID=123456789012345678
   
   # Internal API Security Token (must be identical for bot and backend)
   API_TOKEN=super_secret_internal_token
   
   # PostgreSQL Database Configuration
   POSTGRES_USER=rs_skin
   POSTGRES_PASSWORD=rs_skin_password
   POSTGRES_DB=rs_skin
   
   # Public URL for the bot (used for generating upload links and download links)
   PUBLIC_URL=http://your-domain.com

   # Optional: Path to Assetto Corsa Server Manager (ACSM) cars directory for direct upload mode
   ACSM_LOCAL_PATH=/path/to/your/acsm/server/assetto/content/cars
   ```

3. **Start the services:**
   ```bash
   docker-compose up --build -d
   ```

## 🛠️ Discord Commands

Once the bot is running and invited to your server, you can use the following slash commands:

### User Commands
* `/upload_skin` : Generates a personalized upload link for the user.

### Compilation Commands
* `/build_pack <pack_name>` : Compiles all uploaded skins for the specified championship into a single `.zip` file.
* `/build_pack` *(without argument)* : Checks all championships, compiles the ones with new skins, and returns a summary.

### Pack Management Commands
* `/pack list` : Displays all configured championships and their allowed cars.
* `/pack create <pack_name>` : Creates a new empty championship.
* `/pack delete <pack_name>` : Deletes an existing championship.
* `/pack add_car <pack_name> <car_name>` : Adds an allowed car to a championship (must match the exact AC folder name).
* `/pack remove_car <pack_name> <car_name>` : Removes a car from a championship.

### Configuration Commands
* `/config view` : View the currently active automatic upload mode.
* `/config set_mode <mode>` : Change the automatic behavior when a user uploads a new skin. This is highly useful for managing server load or controlling when skinpacks are distributed. Available modes:
  * ➡️ **`direct` (ACSM Only):** The uploaded skin is instantly extracted and sent directly to the Assetto Corsa Server Manager (ACSM) directory. It becomes available on the live game server immediately. However, the downloadable `.zip` pack for other players is *not* rebuilt automatically, saving server CPU.
  * 📦 **`pack_only` (.zip Pack Only):** The bot automatically recompiles the complete `.zip` skinpack for players to download via Content Manager. The skin is *not* sent to the live game server automatically.
  * 🚀 **`both` (ACSM + Pack):** Performs both actions instantly. The skin is sent to the game server AND the `.zip` pack is rebuilt. Perfect for keeping both the server and players completely up to date in real-time.
  * ⏸️ **`manual` (No auto actions):** The uploaded skin is only saved in the database. No extraction to ACSM and no pack compilation happens automatically. You will need to manually use `/build_pack` to deploy the skins later (ideal during busy races where you want zero background processing).
* `/config summary <mode>` : Configures the frequency and conditions for automated status reports.

## 📁 File Structure

```text
rs-skinbot/
├── backend/               # FastAPI backend source code
│   ├── app/               # Main application logic (builder, db, models)
│   ├── config/            # JSON configuration files (packs.json)
│   ├── Dockerfile         
│   └── requirements.txt   
├── bot/                   # Discord bot source code
│   ├── bot.py             
│   ├── Dockerfile         
│   └── requirements.txt   
├── frontend/              # HTML/CSS/JS for the upload page
├── docker-compose.yml     # Docker services configuration
├── nginx.conf             # Nginx reverse proxy configuration
└── .env                   # Environment variables (not in source control)
```
