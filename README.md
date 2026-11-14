# Digital Lost-and-Found Registry

A web-based platform that helps people report lost and found belongings, discover possible matches, and verify ownership through a structured claim process.

**Live Demo:** https://lost-and-found-registry.vercel.app  
**Repository:** https://github.com/ShaikhVaiz/lost-and-found-registry

## Overview

The Digital Lost-and-Found Registry provides a centralized way to manage lost and found item reports. Instead of depending on manual registers, users can submit reports, search available items, receive possible match suggestions, and submit claims for ownership verification.

The project focuses on simplifying item recovery while protecting private information.

## Features

- **User Authentication:** Registration, login, logout and password recovery.
- **Lost and Found Reports:** Create and manage item reports.
- **Possible Match Detection:** Compare lost and found reports using matching criteria.
- **Ownership Verification:** Submit claims with confidential identifying details.
- **Claim Management:** Track pending, approved and rejected claims.
- **Administrator Dashboard:** Review claims, manage reports and handle exceptional resolutions.
- **Claim Audit History:** Maintain a record of important claim-related actions.
- **Privacy and Security:** Password hashing, CSRF protection and restricted access to administrative functions.

## Technology Stack

| Component | Technology |
|---|---|
| Backend | Python, Flask |
| Database / ORM | SQLAlchemy, Flask-SQLAlchemy |
| Local Database | SQLite |
| Production Database | TiDB Cloud (MySQL-compatible) |
| Frontend | HTML5, CSS3, JavaScript |
| Authentication | Werkzeug password hashing |
| CSRF Protection | Flask-WTF |
| Email OTP | Resend API |
| Deployment | Vercel |

## Application Workflow

1. A user creates an account and logs in.
2. The user submits a lost or found item report.
3. The application identifies possible matches.
4. A claimant submits ownership information.
5. An administrator reviews the claim.
6. An approved claim resolves the linked reports.

## Project Structure

```text
lost-and-found-registry/
├── api/
├── public/
├── static/
├── templates/
├── app.py
├── requirements.txt
├── test_app.py
├── Procfile
├── vercel.json
└── README.md
```

## Local Development

### 1. Clone the repository

```bash
git clone https://github.com/ShaikhVaiz/lost-and-found-registry.git
cd lost-and-found-registry
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

Activate it on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a private `.env` file for local development.

Required configuration:

```env
SECRET_KEY=
ADMIN_ID=
ADMIN_PASSWORD=
```

Optional email configuration:

```env
RESEND_API_KEY=
```

For production database configuration, use the appropriate TiDB Cloud connection variables. Never commit real credentials or `.env` files.

### 5. Run the application

```bash
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Automated Testing

Run the test suite:

```bash
python -m unittest test_app.py
```

The latest recorded local test run completed with **58 tests passing**.

## Deployment

The application is deployed using Vercel.

Production configuration uses environment variables for administrator credentials, application security and persistent database connectivity.

**Live application:** https://lost-and-found-registry.vercel.app

## Security Notes

- Do not store passwords, API keys or database credentials in source code.
- Administrator credentials must be configured through secure environment variables.
- Ownership verification details should be treated as confidential.
- Production data should be backed up before database changes.

## Project Status

**Version:** 1.0.0  
**Status:** Deployed and manually tested

This project was developed as a practical web application project to explore backend development, database integration, authentication, security and deployment.
