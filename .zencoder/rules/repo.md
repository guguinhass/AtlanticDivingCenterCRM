---
description: Repository Information Overview
alwaysApply: true
---

# Atlantic Diving Center CRM Information

## Summary
This is a Customer Relationship Management (CRM) system for Atlantic Diving Center, developed as an internship project. The application manages customer data, automates email communications, and provides feedback collection functionality for diving experiences.

## Structure
- **app.py**: Main Flask application entry point
- **static/**: Directory for static assets (CSS, JS, images)
- **templates/**: HTML templates for the web interface, including email templates
- **instance/**: Instance-specific data (likely contains database)
- **Dockerfile**: Container configuration for deployment
- **requirements.txt**: Python dependencies

## Language & Runtime
**Language**: Python
**Version**: 3.11 (based on Dockerfile)
**Framework**: Flask 2.3.2
**Database**: SQLAlchemy with Supabase integration
**Package Manager**: pip

## Dependencies
**Main Dependencies**:
- Flask (2.3.2): Web framework
- Flask-SQLAlchemy (3.1.1): ORM for database operations
- Supabase (2.17.0): Backend-as-a-Service integration
- Pandas (2.3.1): Data manipulation and analysis
- APScheduler (3.11.0): Task scheduling for automated emails
- Jinja2 (3.1.6): Template engine
- Gunicorn (21.2.0): WSGI HTTP Server for production

**Development Dependencies**:
- python-dotenv (1.1.1): Environment variable management

## Build & Installation
```bash
# Install dependencies
pip install -r requirements.txt

# Run development server
python app.py

# Run with gunicorn (production)
gunicorn -w 4 --timeout 120 -b 0.0.0.0:8000 app:app
```

## Docker
**Dockerfile**: Dockerfile at project root
**Image**: Python 3.11-slim base
**Configuration**: 
- Exposes port 8000
- Uses Gunicorn with 4 workers
- Sets Flask environment to production
- 120 second timeout for long-running operations

**Build & Run**:
```bash
docker build -t atlantic-diving-crm .
docker run -p 8000:8000 atlantic-diving-crm
```

## Application Features
**Email Automation**:
- Scheduled email sending to clients after diving experiences
- Multi-language email templates (Portuguese, English, German, French, etc.)
- Custom email template management
- Configurable SMTP settings

**Database**:
- Customer information storage
- Email tracking (sent status, timestamps)
- Template customization

**Authentication**:
- Login system with session management
- Password hashing for security