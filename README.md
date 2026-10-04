# 🔍 Digital Lost-and-Found Mechanism for Public Spaces

A modern, deployment-ready Flask web application providing a centralized registry to report, search, and algorithmically correlate lost and found belongings across transit terminals, university campuses, municipal libraries, and public community facilities.

---

## 🌟 Key Features

1. **Clean Landing Page & Public Search**:
   - Modern hero section with intuitive action buttons: *Report Lost*, *Report Found*, and *Search Items*.
   - Live statistics overview: Total Reports, Active Lost, Stored Found, and Resolved belongings.
   - Comprehensive multi-filter search: Filter by keyword, category taxonomy, location, date, and status.

2. **User Authentication & Session Management**:
   - Secure registration, login, and logout with Werkzeug password hashing.
   - User Dashboard featuring personal *Lost Reports*, *Found Reports*, and real-time *Possible Match Alerts*.
   - Edit, delete, and resolve actions on user's own reports.

3. **Multi-Factor Possible Matching Engine**:
   - Algorithmic matching scoring (0–100%) between opposing Lost and Found reports:
     - **Category Match**: 35 points
     - **Name & Description Keyword Overlap**: Up to 35 points
     - **Location Similarity**: Up to 20 points
     - **Date Proximity**: Up to 10 points
   - Clearly labeled as **Possible Match** with transparent reason tags (designed to be easily explained during a BSc Computer Science viva/presentation).
   - Dedicated **Possible Matches Matrix** (`/matches`) and individual item match suggestions (`/item/<id>`).

4. **Image Upload & Verification**:
   - Secure image upload (validation for file extension, file size, and sanitized filenames).
   - Confidential *Identifying Details* field (lockscreen photo, serial number, private marks) to safely verify true ownership before item handover.

5. **Simple Administrator Capability**:
   - Role-based `@admin_required` route (`/admin`).
   - Master audit table of all inventory items and registered accounts.
   - One-click capability to change report status (`Active`, `Matched`, `Resolved`) or delete inappropriate/spam reports.

---

## 🛠️ Technology Stack

- **Backend**: Python 3.10+ / Flask 3.x
- **Database / ORM**: SQLAlchemy / Flask-SQLAlchemy (relational schema, SQLite by default, ready for PostgreSQL/MySQL via `DATABASE_URL`)
- **Frontend**: Responsive HTML5 + Pure CSS3 (clean slate design system) + Minimal Vanilla JavaScript
- **Security**: Werkzeug password hashing, session cookies, environment variables via `python-dotenv`
- **Deployment**: Gunicorn WSGI server & Render Procfile

---

## 🚀 How to Run Locally (Windows Desktop)

1. Open PowerShell or Command Prompt:
   ```powershell
   cd C:\Users\Vaiz\OneDrive\Desktop\LostAndFoundHub
   ```

2. (Optional) Create & activate a virtual environment:
   ```powershell
   python -m venv venv
   .\venv\Scripts\activate
   ```

3. Install requirements:
   ```powershell
   pip install -r requirements.txt
   ```

4. Start the application:
   ```powershell
   python app.py
   ```

5. Open your browser at:
   **[http://127.0.0.1:5000](http://127.0.0.1:5000)**

---

## 🔑 Default Pre-Seeded Accounts

| User Type | Email | Password | Role & Permissions |
| :--- | :--- | :--- | :--- |
| **System Admin** | `admin@lostfound.org` | `admin123` | Full inventory moderation, user audit, status override, report deletion |
| **Normal Citizen** | `aarav@example.com` | `user123` | Personal dashboard, report lost/found items, view match notifications |
| **New Citizen** | *(Any email)* | *(Any password)* | Register at `/register` |

*Note: System Administrators log in directly with `admin@lostfound.org` / `admin123`. Real citizen users register with their genuine email at `/register` and can reset their password via email OTP at `/forgot-password`.*

---

## 🗄️ Database Information & Environment Variables

### Local Development:
Uses local SQLite (`sqlite:///lost_found.db`). Created and seeded automatically on first launch.

### TiDB Cloud (Cloud MySQL) Connection:
To connect directly to TiDB Cloud, simply configure `.env`:
```env
# TiDB Cloud Serverless / Dedicated connection
DATABASE_URL=mysql+pymysql://<user>:<password>@<tidb_host>:4000/<database>
```
Or via individual environment variables:
```env
TIDB_HOST=gateway01.ap-southeast-1.prod.aws.tidbcloud.com
TIDB_PORT=4000
TIDB_USER=your_user.root
TIDB_PASSWORD=your_password
TIDB_DATABASE=lost_found
```
*(TiDB Cloud TLS/SSL encryption is auto-negotiated in `app.py`).*

### Real Email Dispatcher via Resend API:
Get your free API key from [Resend](https://resend.com/api-keys) and paste it into `.env`:
```env
RESEND_API_KEY=re_xxxxxxxxxxxxxxxxxxxx
```
When a citizen clicks **Forgot Password**, real 6-digit verification codes are dispatched directly to their email inbox via Resend. If `RESEND_API_KEY` is empty, codes are securely printed to the server console for immediate local testing.

---

## ☁️ Deployment Instructions (Render)

1. Push this project folder to a GitHub repository.
2. In [Render Dashboard](https://dashboard.render.com), click **New +** &rarr; **Web Service**.
3. Select your repository.
4. Set:
   - **Environment**: `Python`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app` (also configured in `Procfile`)
5. Under **Environment Variables**, add:
   - `SECRET_KEY`: A strong random string
   - `DATABASE_URL`: SQLite or your Render PostgreSQL database connection string
6. Click **Deploy Web Service**.

---

## 🧪 Running Automated Tests

Run the complete 14-point test suite:
```powershell
python test_app.py
```
```text
Ran 14 tests in 0.898s
OK
```
Tests verify:
- Application startup & landing page
- Registration & login/logout authentication
- Lost & Found submissions creating database records
- Keyword search and multi-criteria filters
- Item details view with ownership verification
- Possible matching heuristic algorithm calculations
- Image upload validation
- User personal dashboard
- Form input validation & friendly error handling
- Unauthorized route protection
- Admin role enforcement & inventory management
- API stats endpoint & requirements verification.
