# FPL AI Manager

A full-stack Fantasy Premier League assistant built with Django REST Framework, a React website, and a Telegram bot. The project combines live FPL data, squad analysis, player comparison, prediction tools, and automated notifications for managers who want faster decision-making.

This repo is organized so you can work on two main products separately:

- Telegram Bot: chat-first experience for quick squad updates, reminders, and transfer guidance
- Website: browser experience for deeper analysis and player comparison
- Backend API: shared data layer and business logic used by both

---

## Overview

The application is designed to help FPL managers by:

- comparing players and squads
- checking upcoming fixtures and difficulty
- tracking squad form and health
- generating AI-assisted transfer and captain recommendations
- sending deadline reminders and weekly health summaries
- surfacing live and historical FPL data in a clean interface

---

## Architecture

### 1) Telegram Bot
Location: `backend/apps/notifications/` and bot service in `docker-compose.yml`

Responsibilities:
- user onboarding and team linking
- FPL team updates via chat commands
- fixture and squad information
- transfer and captain suggestions
- scheduled reminders and weekly notifications
- multilingual responses

Main bot entry points:
- `backend/apps/notifications/bot.py`
- `backend/apps/notifications/management/commands/runbot.py`
- `backend/apps/notifications/tasks.py`

### 2) Website
Location: `frontend/`

Responsibilities:
- React + Vite frontend
- comparison views and dashboard-style UI
- marketing / landing sections
- public-access product experience for managers

Main frontend files:
- `frontend/src/App.jsx`
- `frontend/src/pages/ComparePlayers.jsx`
- `frontend/src/components/`

### 3) Backend API
Location: `backend/`

Responsibilities:
- Django + DRF backend
- FPL data sync and processing
- API endpoints for frontend and bot integration
- Celery jobs for scheduled syncs and notifications
- user accounts and manager profiles

Main backend areas:
- `backend/config/`
- `backend/apps/fpl_data/`
- `backend/apps/accounts/`
- `backend/apps/predictions/`
- `backend/apps/notifications/`

---

## Tech Stack

- Backend: Django, Django REST Framework, PostgreSQL
- Async jobs: Celery + Redis
- Telegram: python-telegram-bot
- Frontend: React, Vite, Recharts
- Containerization: Docker + Docker Compose

---

## Project Structure

```bash
.
├── backend/
│   ├── apps/
│   │   ├── accounts/
│   │   ├── fpl_data/
│   │   ├── notifications/
│   │   ├── predictions/
│   │   └── ...
│   ├── config/
│   ├── requirements/
│   ├── manage.py
│   ├── Dockerfile
│   └── Dockerfile.prod
├── frontend/
│   ├── src/
│   ├── package.json
│   ├── vite.config.js
│   └── Dockerfile.prod
├── docker-compose.yml
├── docker-compose.prod.yml
├── README.md
└── .env
```

---

## Environment Variables

Create a `.env` file in the project root with the required values. The app expects variables such as:

```env
SECRET_KEY=your-secret-key
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
DB_NAME=fpl_db
DB_USER=fpl_user
DB_PASSWORD=fpl_password
TELEGRAM_BOT_TOKEN=your_telegram_token
GROQ_API_KEY=your_groq_key
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
CORS_ALLOWED_ORIGINS=http://localhost:5173
CSRF_TRUSTED_ORIGINS=http://localhost:5173
```

---

## Running the Project

### Option 1: Docker (recommended)

From the project root:

```bash
docker compose up --build
```

This runs the main services:

- backend Django app
- PostgreSQL database
- Redis
- Celery worker
- Celery beat
- Telegram bot

### Option 2: Local development

#### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements/dev.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

#### Telegram Bot

```bash
cd backend
source .venv/bin/activate
python manage.py runbot
```

#### Frontend

```bash
cd frontend
npm install
npm run dev
```

The React app usually runs on:

- `http://localhost:5173`

The Django API usually runs on:

- `http://localhost:8000`

---

## Telegram Bot vs Website

### Telegram Bot
Best for quick interactions and notifications.

Typical bot use cases:
- connect FPL team by link
- check squad status
- fetch fixtures
- receive reminders before deadline
- get weekly health checks
- ask for transfer/captain recommendations

### Website
Best for richer analysis and visual exploration.

Typical website use cases:
- compare players side by side
- browse analytics and insights
- show more detailed dashboards and UI flows
- serve a broader product experience outside chat

This separation helps you focus on the bot independently without mixing the bot logic with the frontend UI.

---

## Common Tasks

### Working on the Telegram bot
Focus on:
- `backend/apps/notifications/bot.py`
- `backend/apps/notifications/tasks.py`
- `backend/apps/notifications/translations.py`
- `backend/apps/accounts/services.py`

### Working on the website
Focus on:
- `frontend/src/`
- `frontend/src/components/`
- `frontend/src/pages/`
- `frontend/src/api/`

### Working on the backend API
Focus on:
- `backend/apps/fpl_data/`
- `backend/apps/predictions/`
- `backend/apps/accounts/`
- `backend/config/settings.py`

---

## Production Notes

For production, the repo includes a production compose file:

```bash
docker compose -f docker-compose.prod.yml up --build
```

This is intended for deployment with the Django app behind a production-style setup and the frontend served by the production container config.

---

## License

This project is currently intended for personal or internal use unless otherwise specified by the owner.

---

## Future Improvements

Potential next steps:
- add more advanced player comparison analytics
- improve bot command UX and inline keyboards
- add user auth flows for website accounts
- expand AI decision support for transfers and captaincy
- broaden multilingual support and notifications

---

## Notes

The project is already structured around a shared backend and two frontends: one in Telegram and one in the browser. That makes it easy to evolve each product independently while keeping the same core data and forecasting logic.

