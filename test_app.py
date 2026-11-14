import unittest
import unittest.mock
import os
import sys
import io
import re

from werkzeug.security import generate_password_hash
from app import (
    app, db, User, Report, PasswordReset, Claim, ClaimAudit,
    run_schema_migrations, calculate_match_score, sanitize_error_msg, internal_error, get_debug_mode, ADMIN_ID, ADMIN_PASSWORD
)

class DigitalLostAndFoundTestSuite(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()

        with app.app_context():
            ClaimAudit.query.delete()
            Claim.query.delete()
            Report.query.delete()

            # Clean up temporary test users if lingering
            for email in ["teststudent@example.com", "competing@example.com", "malicious@example.com", "audituser@example.com"]:
                u = User.query.filter_by(email=email).first()
                if u:
                    db.session.delete(u)
            db.session.commit()

            # Ensure seed user aarav exists with known password user123
            aarav = User.query.filter_by(email="aarav@example.com").first()
            if not aarav:
                aarav = User(name="Aarav Sharma", email="aarav@example.com", password_hash=generate_password_hash("user123"), role="user", phone="+91 98765 43210")
                db.session.add(aarav)
            else:
                aarav.password_hash = generate_password_hash("user123")

            # Ensure second user priya exists with known password user123
            priya = User.query.filter_by(email="priya@example.com").first()
            if not priya:
                priya = User(name="Priya Patel", email="priya@example.com", password_hash=generate_password_hash("user123"), role="user", phone="+91 98765 11111")
                db.session.add(priya)
            else:
                priya.password_hash = generate_password_hash("user123")

            db.session.commit()

    def tearDown(self):
        with app.app_context():
            ClaimAudit.query.delete()
            Claim.query.delete()
            Report.query.delete()
            for email in ["teststudent@example.com", "competing@example.com", "malicious@example.com", "audituser@example.com"]:
                u = User.query.filter_by(email=email).first()
                if u:
                    db.session.delete(u)
            aarav = User.query.filter_by(email="aarav@example.com").first()
            if aarav:
                aarav.password_hash = generate_password_hash("user123")
            db.session.commit()

    def login_citizen(self):
        self.client.get("/logout")
        return self.client.post("/login", data={
            "email": "aarav@example.com",
            "password": "user123"
        }, follow_redirects=True)

    def login_admin(self):
        self.client.get("/logout")
        return self.client.post("/login", data={
            "login_id": ADMIN_ID,
            "password": ADMIN_PASSWORD
        }, follow_redirects=True)

    def test_a_b_portal_entry_and_authentication_flow(self):
        """A & B: Unauthenticated visitor redirected to Login; Authenticated user accesses Registry Home"""
        # 1. Unauthenticated visiting root redirects to /login
        res_guest = self.client.get("/")
        self.assertEqual(res_guest.status_code, 302)
        self.assertIn("/login", res_guest.headers["Location"])

        # Following redirect leads to Official Portal Sign In
        res_login_page = self.client.get("/", follow_redirects=True)
        self.assertEqual(res_login_page.status_code, 200)
        self.assertIn(b"Official Portal Sign In", res_login_page.data)

        # 2. After official login, home page renders with full dashboard
        self.login_citizen()
        res_home = self.client.get("/")
        self.assertEqual(res_home.status_code, 200)
        self.assertIn(b"Lost Something? Found Something?", res_home.data)

    def test_c_registration_works(self):
        """C: Registration creates account with hashed password and establishes session"""
        reg_data = {
            "name": "Test Student",
            "email": "teststudent@example.com",
            "password": "securepass123",
            "phone": "+91 91111 22222"
        }
        res = self.client.post("/register", data=reg_data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Test Student", res.data)

        with app.app_context():
            u = User.query.filter_by(email="teststudent@example.com").first()
            self.assertIsNotNone(u)
            self.assertEqual(u.name, "Test Student")
            self.assertEqual(u.role, "user")

    def test_d_e_login_and_logout(self):
        """D & E: Login authenticates credentials, sets session, and logout cleans up"""
        # Valid login
        res_login = self.login_citizen()
        self.assertEqual(res_login.status_code, 200)
        self.assertIn(b"Aarav Sharma", res_login.data)

        # Logout
        res_logout = self.client.get("/logout", follow_redirects=True)
        self.assertEqual(res_logout.status_code, 200)
        self.assertIn(b"signed out successfully", res_logout.data)

    def test_f_g_h_lost_and_found_reports_create_records(self):
        """F, G, H: Lost item report and Found item report successfully create DB records"""
        self.login_citizen()

        # Submit Lost Report
        lost_data = {
            "report_type": "lost",
            "title": "MacBook Pro M2 Space Grey",
            "category": "Electronics & Gadgets",
            "description": "Left MacBook inside grey sleeve near study carrel #4",
            "incident_date": "2026-10-04",
            "incident_time": "11:00",
            "location": "Main University Library 3rd Floor",
            "contact_name": "Aarav Sharma",
            "contact_info": "aarav@example.com",
            "identifying_details": "Serial ending in X791"
        }
        res_lost = self.client.post("/report", data=lost_data, follow_redirects=True)
        self.assertEqual(res_lost.status_code, 200)
        self.assertIn(b"MacBook Pro M2 Space Grey", res_lost.data)

        # Submit Found Report
        found_data = {
            "report_type": "found",
            "title": "Sony Wireless Earbuds White",
            "category": "Electronics & Gadgets",
            "description": "White earbuds found inside charging cradle on cafeteria table",
            "incident_date": "2026-10-04",
            "incident_time": "12:30",
            "location": "Central Campus Cafeteria",
            "contact_name": "Helpdesk Staff",
            "contact_info": "desk@campus.edu",
            "identifying_details": "Slight scuff mark on lid"
        }
        res_found = self.client.post("/report", data=found_data, follow_redirects=True)
        self.assertEqual(res_found.status_code, 200)
        self.assertIn(b"Sony Wireless Earbuds White", res_found.data)

        # Verify DB records
        with app.app_context():
            rep1 = Report.query.filter_by(title="MacBook Pro M2 Space Grey").first()
            rep2 = Report.query.filter_by(title="Sony Wireless Earbuds White").first()
            self.assertIsNotNone(rep1)
            self.assertIsNotNone(rep2)
            self.assertEqual(rep1.report_type, "lost")
            self.assertEqual(rep2.report_type, "found")

    def test_i_j_search_and_filters(self):
        """I & J: Search by keyword and category/status filters work for authenticated users"""
        self.login_citizen()

        with app.app_context():
            u = User.query.filter_by(email="aarav@example.com").first()
            r = Report(
                user_id=u.id,
                report_type="lost",
                title="Apple iPhone Blue",
                category="Electronics & Gadgets",
                description="Lost blue apple iphone",
                incident_date="2026-10-04",
                location="Library",
                contact_name="Aarav",
                contact_info="aarav@example.com",
                status="Active"
            )
            db.session.add(r)
            db.session.commit()

        res_keyword = self.client.get("/search?q=iPhone")
        self.assertEqual(res_keyword.status_code, 200)
        self.assertIn(b"iPhone", res_keyword.data)

        res_type = self.client.get("/search?type=lost")
        self.assertEqual(res_type.status_code, 200)

        res_cat = self.client.get("/search?category=Electronics%20%26%20Gadgets")
        self.assertEqual(res_cat.status_code, 200)

        res_status = self.client.get("/search?status=Active")
        self.assertEqual(res_status.status_code, 200)

    def test_k_item_details_page(self):
        """K: Item details page loads with complete info and possible match evaluation"""
        self.login_citizen()

        with app.app_context():
            u = User.query.filter_by(email="aarav@example.com").first()
            test_report = Report(
                user_id=u.id,
                report_type="lost",
                title="Black Leather Laptop Sleeve",
                category="Bags & Backpacks",
                description="Lost leather sleeve with documents inside",
                incident_date="2026-10-04",
                location="Main Reading Room Desk 12",
                contact_name="Aarav Sharma",
                contact_info="aarav@example.com",
                status="Active"
            )
            db.session.add(test_report)
            db.session.commit()
            item_id = test_report.id

        res = self.client.get(f"/item/{item_id}")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Report Status", res.data)
        self.assertIn(b"Possible Matches Detected", res.data)

    def test_l_possible_matching_algorithm(self):
        """L: Multi-factor possible matching calculates realistic match percentage"""
        with app.app_context():
            item_lost = Report(
                report_type="lost",
                title="Midnight Blue iPhone 13 Pro",
                category="Electronics & Gadgets",
                description="Lost blue apple smartphone in library cafeteria",
                incident_date="2026-10-04",
                location="Central City Library 2nd Floor",
                contact_name="Test Citizen",
                contact_info="citizen@example.com"
            )
            item_found = Report(
                report_type="found",
                title="Apple iPhone Blue Color",
                category="Electronics & Gadgets",
                description="Found blue phone on library cafeteria table",
                incident_date="2026-10-04",
                location="Central City Library Cafeteria",
                contact_name="Library Reception",
                contact_info="desk@library.org"
            )
            score, reasons = calculate_match_score(item_lost, item_found)
            self.assertGreaterEqual(score, 60, f"Expected high score for phone match, got {score}")
            self.assertTrue(any("Category" in r for r in reasons))
            self.assertTrue(any("Location" in r for r in reasons))

    def test_m_image_upload_works(self):
        """M: Image upload validation and file save works"""
        self.login_citizen()
        fake_photo = (io.BytesIO(b"fake image content"), "uploaded_gadget.jpg")
        upload_data = {
            "report_type": "lost",
            "title": "Smart Fitness Band Black",
            "category": "Electronics & Gadgets",
            "description": "Black silicone strap fitness band",
            "incident_date": "2026-10-04",
            "incident_time": "09:00",
            "location": "Civic Sports Complex Gym",
            "contact_name": "Aarav Sharma",
            "contact_info": "aarav@example.com",
            "photo": fake_photo
        }
        res = self.client.post("/report", data=upload_data, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Smart Fitness Band Black", res.data)

        with app.app_context():
            band = Report.query.filter_by(title="Smart Fitness Band Black").first()
            self.assertIsNotNone(band)
            self.assertIsNotNone(band.image_filename)

    def test_n_dashboard_works(self):
        """N: User dashboard loads with personal reports"""
        self.login_citizen()
        res = self.client.get("/dashboard")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"My Lost Belongings", res.data)
        self.assertIn(b"Belongings I Found", res.data)

    def test_o_invalid_input_handled(self):
        """O: Empty required fields display friendly error message"""
        self.login_citizen()
        invalid_data = {
            "report_type": "lost",
            "title": "", # Missing required title
            "category": "Electronics & Gadgets",
            "description": "Test",
            "incident_date": "2026-10-04",
            "location": "Test place",
            "contact_name": "Aarav",
            "contact_info": "aarav@example.com"
        }
        res = self.client.post("/report", data=invalid_data)
        self.assertIn(b"Please fill in all mandatory fields", res.data)

    def test_p_unauthorized_pages_protected(self):
        """P: Unauthenticated access to protected routes redirects to login"""
        self.client.get("/logout")
        for endpoint in ["/report", "/dashboard", "/search", "/matches"]:
            res = self.client.get(endpoint)
            self.assertEqual(res.status_code, 302)
            self.assertIn("/login", res.headers["Location"])

    def test_q_admin_functionality_works(self):
        """Q: Admin panel accessible only to admin and blocks standard citizen"""
        # Citizen blocked from admin panel
        self.login_citizen()
        res_blocked = self.client.get("/admin", follow_redirects=True)
        self.assertIn(b"Administrator clearance required", res_blocked.data)

        # Admin access granted
        self.client.get("/logout")
        self.login_admin()
        res_admin = self.client.get("/admin")
        self.assertEqual(res_admin.status_code, 200)
        self.assertIn(b"Administrator Panel", res_admin.data)
        self.assertIn(b"Master Reports Inventory", res_admin.data)
        self.assertIn(b"Registered Users", res_admin.data)

    def test_r_matches_matrix_route(self):
        """R: Global matches matrix route functions cleanly for authenticated user"""
        self.login_citizen()
        res = self.client.get("/matches")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Possible Matches Matrix", res.data)

    def test_s_t_api_stats_and_requirements(self):
        """S & T: API stats endpoint and requirements verification"""
        res_api = self.client.get("/api/stats")
        self.assertEqual(res_api.status_code, 200)
        data = res_api.get_json()
        self.assertIn("total", data)
        self.assertIn("lost", data)
        self.assertIn("found", data)

        req_path = os.path.join(os.path.dirname(__file__), "requirements.txt")
        self.assertTrue(os.path.exists(req_path))
        with open(req_path, "r") as f:
            content = f.read()
        self.assertIn("Flask", content)
        self.assertIn("Flask-SQLAlchemy", content)
        self.assertIn("python-dotenv", content)

    def test_u_forgot_password_and_otp_flow(self):
        """U: Full Forgot Password flow - Email submission, OTP generation, OTP verification, and password reset"""
        # 1. Request reset
        res_get = self.client.get("/forgot-password")
        self.assertEqual(res_get.status_code, 200)
        self.assertIn(b"Forgot Password", res_get.data)

        # 2. Submit email to receive OTP
        res_post = self.client.post("/forgot-password", data={"email": "aarav@example.com"}, follow_redirects=True)
        self.assertEqual(res_post.status_code, 200)
        self.assertIn(b"Verify Code", res_post.data)

        # Check DB for generated OTP
        with app.app_context():
            pr = PasswordReset.query.filter_by(email="aarav@example.com", used=False).order_by(PasswordReset.id.desc()).first()
            self.assertIsNotNone(pr)
            otp_code = pr.otp
            self.assertEqual(len(otp_code), 6)

        # 3. Submit wrong OTP
        res_wrong = self.client.post("/verify-otp", data={
            "otp": "000000",
            "new_password": "newpass123",
            "confirm_password": "newpass123"
        })
        self.assertIn(b"Invalid or expired verification code", res_wrong.data)

        # 4. Submit correct OTP and update password
        res_correct = self.client.post("/verify-otp", data={
            "otp": otp_code,
            "new_password": "newpass123",
            "confirm_password": "newpass123"
        }, follow_redirects=True)
        self.assertEqual(res_correct.status_code, 200)
        self.assertIn(b"successfully reset", res_correct.data)

        # 5. Log in with new password
        res_new_login = self.client.post("/login", data={
            "email": "aarav@example.com",
            "password": "newpass123"
        }, follow_redirects=True)
        self.assertEqual(res_new_login.status_code, 200)
        self.assertIn(b"Aarav Sharma", res_new_login.data)

    def test_v_public_user_cannot_register_as_admin(self):
        """V: Verify that public registration rejects user-submitted admin role and always creates normal user."""
        with app.app_context():
            u_prev = User.query.filter_by(email="malicious@example.com").first()
            if u_prev:
                db.session.delete(u_prev)
                db.session.commit()

        malicious_data = {
            "name": "Malicious User",
            "email": "malicious@example.com",
            "password": "attackpass123",
            "phone": "+91 98888 77777",
            "role": "admin"  # Tampered form field attempting privilege escalation
        }
        res = self.client.post("/register", data=malicious_data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        with app.app_context():
            user = User.query.filter_by(email="malicious@example.com").first()
            self.assertIsNotNone(user)
            # Role must strictly be 'user', never 'admin'
            self.assertEqual(user.role, "user")
            self.assertNotEqual(user.role, "admin")

        # Attempt to access protected admin endpoint with this newly registered account
        admin_res = self.client.get("/admin", follow_redirects=False)
        self.assertIn(admin_res.status_code, [302, 403])
        if admin_res.status_code == 302:
            self.assertNotIn("/admin", admin_res.headers.get("Location", ""))

        # Clean up test user
        with app.app_context():
            u_cleanup = User.query.filter_by(email="malicious@example.com").first()
            if u_cleanup:
                db.session.delete(u_cleanup)
                db.session.commit()

    def test_w_environment_configuration_required(self):
        """W: Verify that missing SECRET_KEY or ADMIN credentials raises RuntimeError without insecure fallbacks."""
        import subprocess, sys
        code = """
import os
os.environ['SECRET_KEY'] = ''
os.environ['ADMIN_ID'] = ''
os.environ['ADMIN_PASSWORD'] = ''
import app
"""
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True,
            text=True
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue("RuntimeError" in proc.stderr or "is not configured" in proc.stderr)

    def test_x_sqlite_foreign_keys_enforcement(self):
        """X: Verify that SQLite PRAGMA foreign_keys=ON is active for database connections."""
        with app.app_context():
            result = db.session.execute(db.text("PRAGMA foreign_keys;")).scalar()
            self.assertEqual(result, 1)

    def test_y1_claim_submission_leaves_status_unchanged(self):
        """Y1: Claim submission creates Pending claim and leaves both report statuses unchanged as Active."""
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()

            found_rep = Report(
                user_id=priya.id,
                report_type="found",
                title="Found Black Leather Wallet",
                category="Wallets & Purses",
                description="Found near Library 2nd floor",
                incident_date="2026-11-01",
                location="Main Campus Library",
                contact_name="Priya Patel",
                contact_info="priya@example.com",
                identifying_details="Contains old silver coin in inner pocket",
                status="Active"
            )
            lost_rep = Report(
                user_id=aarav.id,
                report_type="lost",
                title="Lost Black Leather Wallet",
                category="Wallets & Purses",
                description="Misplaced black wallet somewhere in library",
                incident_date="2026-11-01",
                location="Library Building",
                contact_name="Aarav Sharma",
                contact_info="aarav@example.com",
                status="Active"
            )
            db.session.add_all([found_rep, lost_rep])
            db.session.commit()
            found_id, lost_id = found_rep.id, lost_rep.id

        self.login_citizen()

        res = self.client.post(f"/claim/{found_id}", data={
            "lost_report_id": lost_id,
            "claim_details": "Silver commemorative coin dated 1998 in zipped compartment"
        }, follow_redirects=True)

        self.assertEqual(res.status_code, 200)

        with app.app_context():
            claim = Claim.query.filter_by(found_report_id=found_id, lost_report_id=lost_id).first()
            self.assertIsNotNone(claim)
            self.assertEqual(claim.status, "Pending")
            self.assertIn("Silver commemorative coin", claim.claim_details)

            # Safeguard 1 Verification: Report statuses must remain Active
            f_check = db.session.get(Report, found_id)
            l_check = db.session.get(Report, lost_id)
            self.assertEqual(f_check.status, "Active")
            self.assertEqual(l_check.status, "Active")

    def test_y2_unauthorized_claim_submission_blocked(self):
        """Y2: Non-owner attempting to use another user's lost report for a claim is rejected with 403."""
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()

            found_rep = Report(
                user_id=aarav.id,
                report_type="found",
                title="Found Gold Ring",
                category="Jewelry & Watches",
                description="Found in sports complex",
                incident_date="2026-11-02",
                location="Sports Complex",
                contact_name="Aarav Sharma",
                contact_info="aarav@example.com",
                identifying_details="Initials AS engraved",
                status="Active"
            )
            # Lost report owned by Aarav
            lost_rep = Report(
                user_id=aarav.id,
                report_type="lost",
                title="Lost Gold Ring",
                category="Jewelry & Watches",
                description="Lost ring during training",
                incident_date="2026-11-02",
                location="Sports Complex",
                contact_name="Aarav Sharma",
                contact_info="aarav@example.com",
                status="Active"
            )
            db.session.add_all([found_rep, lost_rep])
            db.session.commit()
            found_id, lost_id = found_rep.id, lost_rep.id

        # Log in as Priya (NOT the owner of lost_rep)
        self.client.post("/login", data={"email": "priya@example.com", "password": "user123"}, follow_redirects=True)

        # Priya attempts to claim found_rep using Aarav's lost report ID
        res = self.client.post(f"/claim/{found_id}", data={
            "lost_report_id": lost_id,
            "claim_details": "Attempted unauthorized claim"
        })
        self.assertEqual(res.status_code, 403)

        with app.app_context():
            claim = Claim.query.filter_by(found_report_id=found_id, lost_report_id=lost_id).first()
            self.assertIsNone(claim)

    def test_y3_duplicate_claim_prevented_by_unique_constraint(self):
        """Y3: UniqueConstraint prevents duplicate claims for the same lost and found report pair."""
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()

            found_rep = Report(
                user_id=priya.id,
                report_type="found",
                title="Found Laptop Charger",
                category="Electronics & Gadgets",
                description="USB-C charger",
                incident_date="2026-11-03",
                location="Cafeteria",
                contact_name="Priya Patel",
                contact_info="priya@example.com",
                status="Active"
            )
            lost_rep = Report(
                user_id=aarav.id,
                report_type="lost",
                title="Lost Laptop Charger",
                category="Electronics & Gadgets",
                description="65W charger",
                incident_date="2026-11-03",
                location="Cafeteria",
                contact_name="Aarav Sharma",
                contact_info="aarav@example.com",
                status="Active"
            )
            db.session.add_all([found_rep, lost_rep])
            db.session.commit()

            # First claim created
            c1 = Claim(
                lost_report_id=lost_rep.id,
                found_report_id=found_rep.id,
                claimant_id=aarav.id,
                claim_details="Has green tape near jack",
                status="Pending"
            )
            db.session.add(c1)
            db.session.commit()

            # Direct duplicate insert must raise IntegrityError
            c2_duplicate = Claim(
                lost_report_id=lost_rep.id,
                found_report_id=found_rep.id,
                claimant_id=aarav.id,
                claim_details="Another claim submission attempt",
                status="Pending"
            )
            db.session.add(c2_duplicate)
            with self.assertRaises(Exception):
                db.session.commit()
            db.session.rollback()

    def test_y4_admin_rejection_leaves_status_unchanged(self):
        """Y4: Admin rejection sets claim status to Rejected, preserves audit notes, and leaves reports Active."""
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()

            found_rep = Report(
                user_id=priya.id,
                report_type="found",
                title="Found Blue Backpack",
                category="Bags & Backpacks",
                description="Blue backpack in hallway",
                incident_date="2026-11-04",
                location="Hallway B",
                contact_name="Priya Patel",
                contact_info="priya@example.com",
                identifying_details="Contains blue water bottle and French notebook",
                status="Active"
            )
            lost_rep = Report(
                user_id=aarav.id,
                report_type="lost",
                title="Lost Blue Backpack",
                category="Bags & Backpacks",
                description="Left bag in hallway",
                incident_date="2026-11-04",
                location="Hallway B",
                contact_name="Aarav Sharma",
                contact_info="aarav@example.com",
                status="Active"
            )
            db.session.add_all([found_rep, lost_rep])
            db.session.commit()

            claim = Claim(
                lost_report_id=lost_rep.id,
                found_report_id=found_rep.id,
                claimant_id=aarav.id,
                claim_details="Contains Spanish notebook and red thermos",
                status="Pending"
            )
            db.session.add(claim)
            db.session.commit()
            claim_id = claim.id
            found_id = found_rep.id
            lost_id = lost_rep.id

        self.login_admin()

        res = self.client.post(f"/admin/claim/{claim_id}/review", data={
            "action": "reject",
            "admin_notes": "Internal contents mismatch (French vs Spanish notebook)"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        with app.app_context():
            c_check = db.session.get(Claim, claim_id)
            self.assertEqual(c_check.status, "Rejected")
            self.assertIn("Internal contents mismatch", c_check.admin_notes)

            # Reports remain Active
            f_check = db.session.get(Report, found_id)
            l_check = db.session.get(Report, lost_id)
            self.assertEqual(f_check.status, "Active")
            self.assertEqual(l_check.status, "Active")

    def test_y5_admin_reopen_rejected_claim(self):
        """Y5: Administrator can reopen a previously rejected claim back to Pending with audit trail."""
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()

            found_rep = Report(
                user_id=priya.id,
                report_type="found",
                title="Found Headphones",
                category="Electronics & Gadgets",
                description="Wireless headphones",
                incident_date="2026-11-05",
                location="Study Room",
                contact_name="Priya Patel",
                contact_info="priya@example.com",
                status="Active"
            )
            lost_rep = Report(
                user_id=aarav.id,
                report_type="lost",
                title="Lost Headphones",
                category="Electronics & Gadgets",
                description="Wireless headphones",
                incident_date="2026-11-05",
                location="Study Room",
                contact_name="Aarav Sharma",
                contact_info="aarav@example.com",
                status="Active"
            )
            db.session.add_all([found_rep, lost_rep])
            db.session.commit()

            claim = Claim(
                lost_report_id=lost_rep.id,
                found_report_id=found_rep.id,
                claimant_id=aarav.id,
                claim_details="Initial insufficient details",
                status="Rejected",
                admin_notes="Insufficient proof"
            )
            db.session.add(claim)
            db.session.commit()
            claim_id = claim.id

        self.login_admin()

        res = self.client.post(f"/admin/claim/{claim_id}/reopen", data={
            "reopen_notes": "Claimant provided purchase receipt via institutional helpdesk"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        with app.app_context():
            c_check = db.session.get(Claim, claim_id)
            self.assertEqual(c_check.status, "Pending")
            self.assertIn("purchase receipt", c_check.admin_notes)

    def test_y6_admin_approval_resolves_both_reports_and_rejects_competing(self):
        """Y6: Claim approval marks both reports Resolved and automatically rejects competing pending claims."""
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()

            # Found report
            found_rep = Report(
                user_id=priya.id,
                report_type="found",
                title="Found Calculator TI-84",
                category="Electronics & Gadgets",
                description="Graphing calculator in room 101",
                incident_date="2026-11-06",
                location="Room 101",
                contact_name="Priya Patel",
                contact_info="priya@example.com",
                identifying_details="Battery compartment has yellow star sticker",
                status="Active"
            )
            # Legitimate claimant Aarav's lost report
            lost_rep_1 = Report(
                user_id=aarav.id,
                report_type="lost",
                title="Lost Graphing Calculator",
                category="Electronics & Gadgets",
                description="TI-84 lost in room 101",
                incident_date="2026-11-06",
                location="Room 101",
                contact_name="Aarav Sharma",
                contact_info="aarav@example.com",
                status="Active"
            )
            # Competing claimant (using another user)
            comp_user = User(
                name="Competing User",
                email="competing@example.com",
                password_hash=generate_password_hash("user123"),
                role="user"
            )
            db.session.add(comp_user)
            db.session.commit()

            lost_rep_2 = Report(
                user_id=comp_user.id,
                report_type="lost",
                title="Lost TI-84 Plus",
                category="Electronics & Gadgets",
                description="Calculator lost",
                incident_date="2026-11-06",
                location="Campus",
                contact_name="Competing User",
                contact_info="competing@example.com",
                status="Active"
            )
            db.session.add_all([found_rep, lost_rep_1, lost_rep_2])
            db.session.commit()

            # Claim 1: legitimate
            c1 = Claim(
                lost_report_id=lost_rep_1.id,
                found_report_id=found_rep.id,
                claimant_id=aarav.id,
                claim_details="Yellow star sticker inside battery door",
                status="Pending"
            )
            # Claim 2: competing
            c2 = Claim(
                lost_report_id=lost_rep_2.id,
                found_report_id=found_rep.id,
                claimant_id=comp_user.id,
                claim_details="Standard black calculator",
                status="Pending"
            )
            db.session.add_all([c1, c2])
            db.session.commit()
            c1_id, c2_id = c1.id, c2.id
            found_id, lost_1_id, lost_2_id = found_rep.id, lost_rep_1.id, lost_rep_2.id

        self.login_admin()

        # Admin approves Claim 1
        res = self.client.post(f"/admin/claim/{c1_id}/review", data={
            "action": "approve",
            "admin_notes": "Sticker verified in battery compartment. Property released."
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        with app.app_context():
            # Claim 1 is Approved
            c1_check = db.session.get(Claim, c1_id)
            self.assertEqual(c1_check.status, "Approved")

            # Both linked reports are now Resolved
            f_check = db.session.get(Report, found_id)
            l1_check = db.session.get(Report, lost_1_id)
            self.assertEqual(f_check.status, "Resolved")
            self.assertEqual(l1_check.status, "Resolved")

            # Competing Claim 2 is automatically Rejected
            c2_check = db.session.get(Claim, c2_id)
            self.assertEqual(c2_check.status, "Rejected")
            self.assertIn("Automatically closed", c2_check.admin_notes)

    def test_y7_prevent_manual_resolution_without_claim(self):
        """Y7: Direct manual resolution via /item/<id>/status is prohibited."""
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            rep = Report(
                user_id=aarav.id,
                report_type="lost",
                title="Bypass Attempt Watch",
                category="Jewelry & Watches",
                description="Watch lost",
                incident_date="2026-11-07",
                location="Gym",
                contact_name="Aarav Sharma",
                contact_info="aarav@example.com",
                status="Active"
            )
            db.session.add(rep)
            db.session.commit()
            rep_id = rep.id

        self.login_citizen()

        # Attempt to set status to Resolved directly
        res = self.client.post(f"/item/{rep_id}/status", data={"status": "Resolved"}, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Direct resolution prohibited", res.data)

        with app.app_context():
            rep_check = db.session.get(Report, rep_id)
            self.assertEqual(rep_check.status, "Active")

    def test_y8_exceptional_admin_resolution_audited(self):
        """Y8: Exceptional in-person resolution records administrator verification and outcome without ID copies."""
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            rep = Report(
                user_id=aarav.id,
                report_type="found",
                title="Found Set of Keys",
                category="Keys & Keychains",
                description="Set of brass keys with red lanyard",
                incident_date="2026-11-08",
                location="Main Gate",
                contact_name="Aarav Sharma",
                contact_info="aarav@example.com",
                status="Active"
            )
            db.session.add(rep)
            db.session.commit()
            rep_id = rep.id

        self.login_admin()

        res = self.client.post(f"/admin/item/{rep_id}/exceptional-resolution", data={
            "outcome_reason": "Owner tested key in bicycle lock in presence of security supervisor"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        with app.app_context():
            rep_check = db.session.get(Report, rep_id)
            self.assertEqual(rep_check.status, "Resolved")
            self.assertIsNotNone(rep_check.admin_resolution_notes)
            self.assertIn("Exceptional In-Person Verification", rep_check.admin_resolution_notes)
            self.assertIn("tested key in bicycle lock", rep_check.admin_resolution_notes)

    def test_y9_foreign_key_deletion_protection(self):
        """Y9: Report linked to claim history is protected against accidental deletion by RESTRICT constraint."""
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()

            found_rep = Report(
                user_id=priya.id,
                report_type="found",
                title="Protected Found Camera",
                category="Electronics & Gadgets",
                description="DSLR Camera",
                incident_date="2026-11-09",
                location="Auditorium",
                contact_name="Priya Patel",
                contact_info="priya@example.com",
                status="Active"
            )
            lost_rep = Report(
                user_id=aarav.id,
                report_type="lost",
                title="Protected Lost Camera",
                category="Electronics & Gadgets",
                description="DSLR Camera",
                incident_date="2026-11-09",
                location="Auditorium",
                contact_name="Aarav Sharma",
                contact_info="aarav@example.com",
                status="Active"
            )
            db.session.add_all([found_rep, lost_rep])
            db.session.commit()

            claim = Claim(
                lost_report_id=lost_rep.id,
                found_report_id=found_rep.id,
                claimant_id=aarav.id,
                claim_details="Custom strap with monogram",
                status="Pending"
            )
            db.session.add(claim)
            db.session.commit()
            rep_id = found_rep.id

        self.login_admin()

        # Deletion attempt via route must be prohibited
        res = self.client.post(f"/item/{rep_id}/delete", follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Permanent deletion prohibited", res.data)

        with app.app_context():
            rep_check = db.session.get(Report, rep_id)
            self.assertIsNotNone(rep_check)

    def test_z_zero_exposure_of_confidential_proof(self):
        """Z: Confidential proof and claimant answers are shielded from public and unauthorized views."""
        with app.app_context():
            priya = User.query.filter_by(email="priya@example.com").first()
            aarav = User.query.filter_by(email="aarav@example.com").first()

            found_rep = Report(
                user_id=priya.id,
                report_type="found",
                title="Found Diary",
                category="Books & Stationery",
                description="Leather journal",
                incident_date="2026-11-10",
                location="Park Bench",
                contact_name="Priya Patel",
                contact_info="priya@example.com",
                identifying_details="CONFIDENTIAL_FINDER_MARK_777",
                status="Active"
            )
            lost_rep = Report(
                user_id=aarav.id,
                report_type="lost",
                title="Lost Diary",
                category="Books & Stationery",
                description="Personal journal",
                incident_date="2026-11-10",
                location="Park",
                contact_name="Aarav Sharma",
                contact_info="aarav@example.com",
                status="Active"
            )
            db.session.add_all([found_rep, lost_rep])
            db.session.commit()

            claim = Claim(
                lost_report_id=lost_rep.id,
                found_report_id=found_rep.id,
                claimant_id=aarav.id,
                claim_details="SECRET_CLAIMANT_ANSWER_888",
                status="Pending"
            )
            db.session.add(claim)
            db.session.commit()
            found_id = found_rep.id

        # 1. Unauthenticated citizen inspecting details cannot see secret proof or claim answer
        res_guest = self.client.get(f"/item/{found_id}")
        self.assertNotIn(b"CONFIDENTIAL_FINDER_MARK_777", res_guest.data)
        self.assertNotIn(b"SECRET_CLAIMANT_ANSWER_888", res_guest.data)

        # 2. Authenticated claimant (Aarav) cannot see the finder's secret proof on the found item page
        self.login_citizen()
        res_claimant = self.client.get(f"/item/{found_id}")
        self.assertNotIn(b"CONFIDENTIAL_FINDER_MARK_777", res_claimant.data)

        # 3. Public search does NOT index confidential proof (cannot be enumerated or matched)
        res_search_proof = self.client.get("/search?q=CONFIDENTIAL_FINDER_MARK_777")
        self.assertNotIn(b"Found Diary", res_search_proof.data)

        # 4. Legitimate public search finds item, but never reveals secret proof or claimant answers in HTML
        res_search_public = self.client.get("/search?q=Diary")
        self.assertIn(b"Found Diary", res_search_public.data)
        self.assertNotIn(b"CONFIDENTIAL_FINDER_MARK_777", res_search_public.data)
        self.assertNotIn(b"SECRET_CLAIMANT_ANSWER_888", res_search_public.data)

    def test_csrf_rejection_when_token_missing_or_invalid(self):
        """Verify that POST requests are rejected with 400 Bad Request when CSRF token is missing or invalid."""
        try:
            app.config["WTF_CSRF_ENABLED"] = True
            # 1. Missing CSRF token
            res_no_token = self.client.post("/login", data={"email": "aarav@example.com", "password": "user123"})
            self.assertEqual(res_no_token.status_code, 400)
            self.assertIn(b"CSRF", res_no_token.data)

            # 2. Invalid CSRF token
            res_invalid = self.client.post("/login", data={"csrf_token": "tampered_csrf_token_value", "email": "aarav@example.com", "password": "user123"})
            self.assertEqual(res_invalid.status_code, 400)
            self.assertIn(b"CSRF", res_invalid.data)
        finally:
            app.config["WTF_CSRF_ENABLED"] = False

    def test_csrf_success_with_valid_token(self):
        """Verify that POST requests succeed when a valid CSRF token is supplied from the rendered form."""
        try:
            app.config["WTF_CSRF_ENABLED"] = True
            res_get = self.client.get("/login")
            self.assertEqual(res_get.status_code, 200)

            # Extract csrf_token from the HTML input field
            import re
            match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', res_get.data.decode("utf-8"))
            self.assertIsNotNone(match, "CSRF token hidden input must be rendered in HTML form")
            token = match.group(1)

            res_post = self.client.post("/login", data={
                "csrf_token": token,
                "email": "aarav@example.com",
                "password": "user123"
            }, follow_redirects=True)
            self.assertEqual(res_post.status_code, 200)
            self.assertIn(b"Aarav Sharma", res_post.data)
        finally:
            app.config["WTF_CSRF_ENABLED"] = False

    def test_audit_history_preservation_after_reviews_and_reopenings(self):
        """Verify that claim audit history is append-only, permanent, never overwritten, and records actions."""
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()

            found = Report(user_id=priya.id, report_type="found", title="Audit Watch", category="Jewelry & Watches", description="Watch found", incident_date="2026-11-01", location="Desk", contact_name="Priya", contact_info="priya@example.com", identifying_details="SECRET_WATCH_PROOF", status="Active")
            lost = Report(user_id=aarav.id, report_type="lost", title="Audit Watch", category="Jewelry & Watches", description="Watch lost", incident_date="2026-11-01", location="Desk", contact_name="Aarav", contact_info="aarav@example.com", status="Active")
            db.session.add_all([found, lost])
            db.session.commit()
            found_id, lost_id = found.id, lost.id

        # 1. Citizen submits claim -> SUBMITTED audit log
        self.login_citizen()
        res_claim = self.client.post(f"/claim/{found_id}", data={
            "lost_report_id": lost_id,
            "claim_details": "Silver bezel with scratch at 3 o'clock"
        }, follow_redirects=True)
        self.assertEqual(res_claim.status_code, 200)

        with app.app_context():
            claim = Claim.query.filter_by(found_report_id=found_id, lost_report_id=lost_id).first()
            self.assertIsNotNone(claim)
            claim_id = claim.id
            audits_step1 = ClaimAudit.query.filter_by(claim_id=claim_id).order_by(ClaimAudit.id.asc()).all()
            self.assertEqual(len(audits_step1), 1)
            self.assertEqual(audits_step1[0].action, "SUBMITTED")
            self.assertNotIn("SECRET_WATCH_PROOF", audits_step1[0].reason)

        # 2. Admin rejects claim -> REJECTED audit log
        self.login_admin()
        res_reject = self.client.post(f"/admin/claim/{claim_id}/review", data={
            "action": "reject",
            "admin_notes": "Initial scratch location did not match"
        }, follow_redirects=True)
        self.assertEqual(res_reject.status_code, 200)

        with app.app_context():
            audits_step2 = ClaimAudit.query.filter_by(claim_id=claim_id).order_by(ClaimAudit.id.asc()).all()
            self.assertEqual(len(audits_step2), 2)
            self.assertEqual(audits_step2[0].action, "SUBMITTED")
            self.assertEqual(audits_step2[1].action, "REJECTED")
            self.assertIn("scratch location did not match", audits_step2[1].reason)

        # 3. Admin reopens claim -> REOPENED audit log
        res_reopen = self.client.post(f"/admin/claim/{claim_id}/reopen", data={
            "reopen_notes": "Claimant submitted original purchase invoice via security"
        }, follow_redirects=True)
        self.assertEqual(res_reopen.status_code, 200)

        with app.app_context():
            audits_step3 = ClaimAudit.query.filter_by(claim_id=claim_id).order_by(ClaimAudit.id.asc()).all()
            self.assertEqual(len(audits_step3), 3)
            self.assertEqual(audits_step3[2].action, "REOPENED")
            self.assertIn("purchase invoice", audits_step3[2].reason)

        # 4. Admin approves claim -> APPROVED audit log
        res_approve = self.client.post(f"/admin/claim/{claim_id}/review", data={
            "action": "approve",
            "admin_notes": "Invoice verified against serial number"
        }, follow_redirects=True)
        self.assertEqual(res_approve.status_code, 200)

        with app.app_context():
            audits_step4 = ClaimAudit.query.filter_by(claim_id=claim_id).order_by(ClaimAudit.id.asc()).all()
            self.assertEqual(len(audits_step4), 4)
            actions = [a.action for a in audits_step4]
            self.assertEqual(actions, ["SUBMITTED", "REJECTED", "REOPENED", "APPROVED"])
            # Ensure no confidential proof was ever written to audit reasons
            for a in audits_step4:
                self.assertNotIn("SECRET_WATCH_PROOF", a.reason or "")

    def test_conflicting_admin_reviews_and_multiple_approved_prevention(self):
        """Verify that a found report cannot have multiple approved claims and conflicting reviews are rejected."""
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()
            user3 = User(name="User Three", email="audituser@example.com", password_hash=generate_password_hash("user123"), role="user")
            db.session.add(user3)
            db.session.commit()

            found = Report(user_id=priya.id, report_type="found", title="Shared Found Item", category="Electronics & Gadgets", description="Item found", incident_date="2026-11-02", location="Hall", contact_name="Priya", contact_info="priya@example.com", status="Active")
            lost1 = Report(user_id=aarav.id, report_type="lost", title="Lost Item 1", category="Electronics & Gadgets", description="Item lost", incident_date="2026-11-02", location="Hall", contact_name="Aarav", contact_info="aarav@example.com", status="Active")
            lost2 = Report(user_id=user3.id, report_type="lost", title="Lost Item 2", category="Electronics & Gadgets", description="Item lost", incident_date="2026-11-02", location="Hall", contact_name="User3", contact_info="user3@example.com", status="Active")
            db.session.add_all([found, lost1, lost2])
            db.session.commit()

            claim1 = Claim(lost_report_id=lost1.id, found_report_id=found.id, claimant_id=aarav.id, claim_details="Proof 1", status="Pending")
            claim2 = Claim(lost_report_id=lost2.id, found_report_id=found.id, claimant_id=user3.id, claim_details="Proof 2", status="Pending")
            db.session.add_all([claim1, claim2])
            db.session.commit()
            c1_id, c2_id = claim1.id, claim2.id

        self.login_admin()

        # Admin approves Claim 1
        res1 = self.client.post(f"/admin/claim/{c1_id}/review", data={"action": "approve", "admin_notes": "Claim 1 verified"}, follow_redirects=True)
        self.assertEqual(res1.status_code, 200)

        with app.app_context():
            c1 = db.session.get(Claim, c1_id)
            c2 = db.session.get(Claim, c2_id)
            self.assertEqual(c1.status, "Approved")
            self.assertEqual(c2.status, "Rejected") # automatically closed

        # Second review attempt on Claim 2 (attempting to approve conflicting claim)
        res2 = self.client.post(f"/admin/claim/{c2_id}/review", data={"action": "approve", "admin_notes": "Attempting second approval"}, follow_redirects=True)
        self.assertEqual(res2.status_code, 200)
        self.assertIn(b"Concurrency Notice", res2.data)

        # Attempt to reopen Claim 2 while Claim 1 is Approved
        res3 = self.client.post(f"/admin/claim/{c2_id}/reopen", data={"reopen_notes": "Attempt reopen"}, follow_redirects=True)
        self.assertEqual(res3.status_code, 200)
        self.assertIn(b"Cannot reopen claim", res3.data)

    def test_exceptional_resolution_closes_pending_claims_and_audits(self):
        """Verify that exceptional in-person resolution closes related pending claims and creates audit records."""
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()

            found = Report(user_id=priya.id, report_type="found", title="Exceptional Found Item", category="Electronics & Gadgets", description="Item found", incident_date="2026-11-03", location="Desk", contact_name="Priya", contact_info="priya@example.com", status="Active")
            lost = Report(user_id=aarav.id, report_type="lost", title="Exceptional Lost Item", category="Electronics & Gadgets", description="Item lost", incident_date="2026-11-03", location="Desk", contact_name="Aarav", contact_info="aarav@example.com", status="Active")
            db.session.add_all([found, lost])
            db.session.commit()

            claim = Claim(lost_report_id=lost.id, found_report_id=found.id, claimant_id=aarav.id, claim_details="Proof", status="Pending")
            db.session.add(claim)
            db.session.commit()
            found_id, claim_id = found.id, claim.id

        self.login_admin()

        # Admin triggers exceptional resolution
        res = self.client.post(f"/admin/item/{found_id}/exceptional-resolution", data={
            "outcome_reason": "Owner demonstrated functional password unlock on device in presence of officer"
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"resolved via audited exceptional in-person verification", res.data)

        with app.app_context():
            f_check = db.session.get(Report, found_id)
            c_check = db.session.get(Claim, claim_id)
            self.assertEqual(f_check.status, "Resolved")
            self.assertIn("Exceptional In-Person Verification", f_check.admin_resolution_notes)
            self.assertNotIn("gov", f_check.admin_resolution_notes.lower())
            # Claim must be closed, not left dangling as Pending
            self.assertEqual(c_check.status, "Rejected")
            self.assertIn("Closed: Associated found report", c_check.admin_notes)

            # Check audit record
            audit = ClaimAudit.query.filter_by(claim_id=claim_id, action="EXCEPTIONAL_RESOLVE_CLOSED").first()
            self.assertIsNotNone(audit)
            self.assertIn("password unlock", audit.reason)

    def test_sqlite_foreign_key_enforcement_on_audits_and_claims(self):
        """Verify that foreign keys are enforced by PRAGMA foreign_keys=ON preventing orphan claims/audits."""
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()

            found = Report(user_id=priya.id, report_type="found", title="FK Test Found", category="Keys & Keychains", description="Keys", incident_date="2026-11-04", location="Gate", contact_name="Priya", contact_info="priya@example.com", status="Active")
            lost = Report(user_id=aarav.id, report_type="lost", title="FK Test Lost", category="Keys & Keychains", description="Keys", incident_date="2026-11-04", location="Gate", contact_name="Aarav", contact_info="aarav@example.com", status="Active")
            db.session.add_all([found, lost])
            db.session.commit()

            claim = Claim(lost_report_id=lost.id, found_report_id=found.id, claimant_id=aarav.id, claim_details="Proof", status="Pending")
            db.session.add(claim)
            db.session.commit()

            audit = ClaimAudit(claim_id=claim.id, actor_id=aarav.id, action="SUBMITTED", reason="Test reason")
            db.session.add(audit)
            db.session.commit()

            # Attempt to delete claim while audit references it (ondelete=RESTRICT)
            db.session.delete(claim)
            with self.assertRaises(Exception):
                db.session.commit()
            db.session.rollback()

    def test_run_schema_migrations_preserves_existing_data(self):
        """Verify that run_schema_migrations() is idempotent and preserves all existing database records."""
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            test_rep = Report(user_id=aarav.id, report_type="lost", title="Migration Preservation Item", category="Documents & IDs", description="Important document", incident_date="2026-11-05", location="Office", contact_name="Aarav", contact_info="aarav@example.com", status="Active")
            db.session.add(test_rep)
            db.session.commit()
            rep_id = test_rep.id

            # Run migrations twice
            run_schema_migrations()
            run_schema_migrations()

            # Verify item is intact
            rep = db.session.get(Report, rep_id)
            self.assertIsNotNone(rep)
            self.assertEqual(rep.title, "Migration Preservation Item")

    def test_migration_safety_does_not_drop_password_resets_table(self):
        """Verify password_resets table is never dropped and raises RuntimeError if unsafe."""
        import unittest.mock
        from datetime import datetime
        with app.app_context():
            # 1. Verify existing records in password_resets are preserved across migrations
            reset = PasswordReset(
                email="safe_migration@example.com",
                otp="123456",
                expires_at=datetime.now()
            )
            db.session.add(reset)
            db.session.commit()
            reset_id = reset.id

            run_schema_migrations()

            # Record must still exist (not dropped)
            saved_reset = db.session.get(PasswordReset, reset_id)
            self.assertIsNotNone(saved_reset)
            self.assertEqual(saved_reset.email, "safe_migration@example.com")
            db.session.delete(saved_reset)
            db.session.commit()

            # 2. Simulate legacy table without 'id' column: migration must halt with RuntimeError, NOT drop table
            with unittest.mock.patch("sqlalchemy.inspect") as mock_inspect:
                mock_inspector = unittest.mock.MagicMock()
                mock_inspector.get_table_names.return_value = ["password_resets"]
                mock_inspector.get_columns.return_value = [{"name": "email"}, {"name": "otp"}]
                mock_inspect.return_value = mock_inspector

                with self.assertRaises(RuntimeError) as ctx:
                    run_schema_migrations()
                self.assertIn("missing primary key 'id'", str(ctx.exception))
                self.assertIn("Automatic table destruction is disabled", str(ctx.exception))

    def test_migration_failure_prevents_initialization(self):
        """Verify failed migrations prevent setting _db_initialized=True and abort requests with 500."""
        import unittest.mock
        import app as app_module
        original_flag = app_module._db_initialized

        try:
            app_module._db_initialized = False

            # Mock run_schema_migrations to raise an exception
            with unittest.mock.patch("app.run_schema_migrations", side_effect=RuntimeError("Simulated migration failure")):
                res = self.client.get("/login")
                # Request must fail with 500 Internal Server Error
                self.assertEqual(res.status_code, 500)
                # _db_initialized must remain False
                self.assertFalse(app_module._db_initialized)
        finally:
            app_module._db_initialized = original_flag

    def test_concurrent_competing_claim_approvals_atomic_safeguard(self):
        """Verify database-level unique constraint on approved_found_report_id prevents competing approvals."""
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()
            user3 = User(name="User Three", email="competing@example.com", password_hash=generate_password_hash("user123"), role="user")
            db.session.add(user3)
            db.session.commit()

            found = Report(user_id=priya.id, report_type="found", title="Concurrent Test Item", category="Electronics & Gadgets", description="Item", incident_date="2026-11-06", location="Room 101", contact_name="Priya", contact_info="priya@example.com", status="Active")
            lost1 = Report(user_id=aarav.id, report_type="lost", title="Concurrent Lost 1", category="Electronics & Gadgets", description="Item 1", incident_date="2026-11-06", location="Room 101", contact_name="Aarav", contact_info="aarav@example.com", status="Active")
            lost2 = Report(user_id=user3.id, report_type="lost", title="Concurrent Lost 2", category="Electronics & Gadgets", description="Item 2", incident_date="2026-11-06", location="Room 101", contact_name="User3", contact_info="user3@example.com", status="Active")
            db.session.add_all([found, lost1, lost2])
            db.session.commit()

            claim1 = Claim(lost_report_id=lost1.id, found_report_id=found.id, claimant_id=aarav.id, claim_details="Proof 1", status="Pending")
            claim2 = Claim(lost_report_id=lost2.id, found_report_id=found.id, claimant_id=user3.id, claim_details="Proof 2", status="Pending")
            db.session.add_all([claim1, claim2])
            db.session.commit()
            c1_id, c2_id, found_id = claim1.id, claim2.id, found.id

        # 1. Test database engine unique constraint: attempting to set approved_found_report_id on two claims raises IntegrityError
        with app.app_context():
            c1 = db.session.get(Claim, c1_id)
            c2 = db.session.get(Claim, c2_id)
            c1.approved_found_report_id = found_id
            c2.approved_found_report_id = found_id
            with self.assertRaises(Exception): # Storage engine unique index violation
                db.session.commit()
            db.session.rollback()

        # 2. Test concurrent race condition in review route:
        # Simulate race condition where found_report was already resolved right before claim approval
        self.login_admin()
        with app.app_context():
            f_rep = db.session.get(Report, found_id)
            f_rep.status = "Resolved"
            db.session.commit()

        # Approving claim1 when found_report is already resolved must fail atomically (0 rows updated)
        res = self.client.post(f"/admin/claim/{c1_id}/review", data={"action": "approve"}, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Conflict: This found report has already been Resolved", res.data)

        with app.app_context():
            c1_check = db.session.get(Claim, c1_id)
            self.assertEqual(c1_check.status, "Pending")
            self.assertIsNone(c1_check.approved_found_report_id)

    def test_error_logging_sanitizes_credentials(self):
        """Verify sanitize_error_msg masks database passwords, credentials, and API secrets."""
        # 1. Database URI with username and password
        err1 = "OperationalError: (2003, 'Can\\'t connect to MySQL server on mysql://admin_user:P@ssword123!@gateway01.tidbcloud.com:4000/registry')"
        sanitized1 = sanitize_error_msg(err1)
        self.assertNotIn("P@ssword123!", sanitized1)
        self.assertIn("mysql://admin_user:****@gateway01.tidbcloud.com:4000/registry", sanitized1)

        # 2. Key-value password parameter
        err2 = "Authentication failed with password=SuperSecretPassword456 and host=localhost"
        sanitized2 = sanitize_error_msg(err2)
        self.assertNotIn("SuperSecretPassword456", sanitized2)
        self.assertIn("password=****", sanitized2)

        # 3. Secret parameter
        err3 = "Invalid token configuration: secret=VeryConfidentialSecretKey789"
        sanitized3 = sanitize_error_msg(err3)
        self.assertNotIn("VeryConfidentialSecretKey789", sanitized3)
        self.assertIn("secret=****", sanitized3)

    def test_production_database_failure_does_not_switch_to_sqlite(self):
        """Verify production DB failure does not switch to SQLite and returns 500 error."""
        import unittest.mock
        import app as app_module
        original_db_url = app_module.app.config["SQLALCHEMY_DATABASE_URI"]
        original_initialized = app_module._db_initialized
        original_vercel = os.environ.get("VERCEL")

        try:
            os.environ["VERCEL"] = "1"
            app_module.app.config["SQLALCHEMY_DATABASE_URI"] = "mysql+pymysql://testuser:testpass@gateway.tidbcloud.com:4000/lost_found"
            app_module._db_initialized = False

            with unittest.mock.patch("app.run_schema_migrations", side_effect=Exception("Connection refused")):
                res = self.client.get("/login")
                # Must abort with 500 server error
                self.assertEqual(res.status_code, 500)
                # Must NOT switch URI to SQLite
                self.assertNotIn("sqlite", app_module.app.config["SQLALCHEMY_DATABASE_URI"])
                self.assertIn("mysql", app_module.app.config["SQLALCHEMY_DATABASE_URI"])
                # Must NOT mark DB as initialized
                self.assertFalse(app_module._db_initialized)
        finally:
            app_module.app.config["SQLALCHEMY_DATABASE_URI"] = original_db_url
            app_module._db_initialized = original_initialized
            if original_vercel is not None:
                os.environ["VERCEL"] = original_vercel
            else:
                os.environ.pop("VERCEL", None)

    def test_local_development_uses_sqlite(self):
        """Verify local development can use SQLite database safely."""
        with app.app_context():
            self.assertTrue(app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"))
            with db.engine.connect() as conn:
                result = conn.execute(db.text("SELECT 1;")).scalar()
                self.assertEqual(result, 1)

    def test_pyd_hook_is_removed(self):
        """Verify the custom AppLocker / unsigned .pyd fallback code is completely removed."""
        import app as app_module
        self.assertFalse(hasattr(app_module, "_SafeExtensionLoader"))
        hook_names = [getattr(h, "__name__", str(h)) for h in sys.path_hooks]
        for name in hook_names:
            self.assertNotIn("SafeExtensionLoader", name)

    def test_password_reset_migration_preserves_records_safely(self):
        """Verify existing password-reset records survive migrations and missing columns are safely handled."""
        from datetime import datetime
        with app.app_context():
            reset = PasswordReset(
                email="preserve_test@example.com",
                otp="987654",
                expires_at=datetime.now(),
                used=False
            )
            db.session.add(reset)
            db.session.commit()
            r_id = reset.id

            run_schema_migrations()

            persisted = db.session.get(PasswordReset, r_id)
            self.assertIsNotNone(persisted)
            self.assertEqual(persisted.email, "preserve_test@example.com")
            self.assertEqual(persisted.otp, "987654")
            self.assertFalse(persisted.used)

            db.session.delete(persisted)
            db.session.commit()

    def test_claim_approval_rollback_preserves_states_on_concurrency_conflict(self):
        """Verify claim approval rollback preserves report and claim states when a concurrency conflict occurs."""
        import unittest.mock
        from sqlalchemy.exc import IntegrityError

        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()

            found = Report(user_id=priya.id, report_type="found", title="Rollback Test Found", category="Electronics & Gadgets", description="Item", incident_date="2026-11-07", location="Hall", contact_name="Priya", contact_info="priya@example.com", status="Active")
            lost = Report(user_id=aarav.id, report_type="lost", title="Rollback Test Lost", category="Electronics & Gadgets", description="Item", incident_date="2026-11-07", location="Hall", contact_name="Aarav", contact_info="aarav@example.com", status="Active")
            db.session.add_all([found, lost])
            db.session.commit()

            claim = Claim(lost_report_id=lost.id, found_report_id=found.id, claimant_id=aarav.id, claim_details="Secret Details", status="Pending")
            db.session.add(claim)
            db.session.commit()
            c_id, f_id, l_id = claim.id, found.id, lost.id

        self.login_admin()

        # Simulate IntegrityError during db.session.commit in claim review
        with unittest.mock.patch("app.db.session.commit", side_effect=IntegrityError("Simulated DB conflict", params=None, orig=Exception("Unique constraint violation"))):
            res = self.client.post(f"/admin/claim/{c_id}/review", data={"action": "approve"}, follow_redirects=True)
            self.assertEqual(res.status_code, 200)
            self.assertIn(b"Concurrency Conflict", res.data)

        # Verify rollback preserved report and claim states
        with app.app_context():
            f_check = db.session.get(Report, f_id)
            l_check = db.session.get(Report, l_id)
            c_check = db.session.get(Claim, c_id)

            self.assertEqual(f_check.status, "Active")  # Must NOT be Resolved
            self.assertEqual(l_check.status, "Active")  # Must NOT be Resolved
            self.assertEqual(c_check.status, "Pending") # Must NOT be Approved
            self.assertIsNone(c_check.approved_found_report_id)

    def test_single_historical_approved_claim_backfilled(self):
        """Verify one historical approved claim with NULL approved_found_report_id is correctly backfilled."""
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()

            found = Report(user_id=priya.id, report_type="found", title="Single Backfill Found", category="Electronics & Gadgets", description="Item", incident_date="2026-11-08", location="Hall", contact_name="Priya", contact_info="priya@example.com", status="Resolved")
            lost = Report(user_id=aarav.id, report_type="lost", title="Single Backfill Lost", category="Electronics & Gadgets", description="Item", incident_date="2026-11-08", location="Hall", contact_name="Aarav", contact_info="aarav@example.com", status="Resolved")
            db.session.add_all([found, lost])
            db.session.commit()

            claim = Claim(lost_report_id=lost.id, found_report_id=found.id, claimant_id=aarav.id, claim_details="Historical Proof", status="Approved", approved_found_report_id=None)
            db.session.add(claim)
            db.session.commit()
            c_id, f_id = claim.id, found.id

            run_schema_migrations()
            db.session.expire_all()

            c_check = db.session.get(Claim, c_id)
            self.assertEqual(c_check.approved_found_report_id, f_id)
            self.assertEqual(c_check.status, "Approved")

    def test_multiple_historical_approved_claims_different_found_reports_backfilled(self):
        """Verify multiple historical approved claims for different found reports are all correctly backfilled."""
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()

            found1 = Report(user_id=priya.id, report_type="found", title="Multi Found 1", category="Electronics & Gadgets", description="Item 1", incident_date="2026-11-08", location="Hall", contact_name="Priya", contact_info="priya@example.com", status="Resolved")
            lost1 = Report(user_id=aarav.id, report_type="lost", title="Multi Lost 1", category="Electronics & Gadgets", description="Item 1", incident_date="2026-11-08", location="Hall", contact_name="Aarav", contact_info="aarav@example.com", status="Resolved")
            found2 = Report(user_id=priya.id, report_type="found", title="Multi Found 2", category="Wallets & Purses", description="Item 2", incident_date="2026-11-08", location="Cafe", contact_name="Priya", contact_info="priya@example.com", status="Resolved")
            lost2 = Report(user_id=aarav.id, report_type="lost", title="Multi Lost 2", category="Wallets & Purses", description="Item 2", incident_date="2026-11-08", location="Cafe", contact_name="Aarav", contact_info="aarav@example.com", status="Resolved")
            db.session.add_all([found1, lost1, found2, lost2])
            db.session.commit()

            claim1 = Claim(lost_report_id=lost1.id, found_report_id=found1.id, claimant_id=aarav.id, claim_details="Proof 1", status="Approved", approved_found_report_id=None)
            claim2 = Claim(lost_report_id=lost2.id, found_report_id=found2.id, claimant_id=aarav.id, claim_details="Proof 2", status="Approved", approved_found_report_id=None)
            db.session.add_all([claim1, claim2])
            db.session.commit()
            c1_id, f1_id = claim1.id, found1.id
            c2_id, f2_id = claim2.id, found2.id

            run_schema_migrations()
            db.session.expire_all()

            c1_check = db.session.get(Claim, c1_id)
            c2_check = db.session.get(Claim, c2_id)
            self.assertEqual(c1_check.approved_found_report_id, f1_id)
            self.assertEqual(c1_check.status, "Approved")
            self.assertEqual(c2_check.approved_found_report_id, f2_id)
            self.assertEqual(c2_check.status, "Approved")

    def test_duplicate_historical_approved_claims_stops_migration_and_preserves_records(self):
        """Verify two approved claims for same found report halts migration with RuntimeError and preserves all records."""
        with app.app_context():
            # Temporarily drop index if it exists to simulate pre-migration duplicate state
            with db.engine.connect() as conn:
                conn.execute(db.text("DROP INDEX IF EXISTS uq_claims_approved_found;"))
                conn.commit()

            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()
            user3 = User(name="User Three", email="competing@example.com", password_hash=generate_password_hash("user123"), role="user")
            db.session.add(user3)
            db.session.commit()

            found = Report(user_id=priya.id, report_type="found", title="Dup Found Item", category="Electronics & Gadgets", description="Item", incident_date="2026-11-09", location="Hall", contact_name="Priya", contact_info="priya@example.com", status="Resolved")
            lost1 = Report(user_id=aarav.id, report_type="lost", title="Dup Lost 1", category="Electronics & Gadgets", description="Item 1", incident_date="2026-11-09", location="Hall", contact_name="Aarav", contact_info="aarav@example.com", status="Resolved")
            lost2 = Report(user_id=user3.id, report_type="lost", title="Dup Lost 2", category="Electronics & Gadgets", description="Item 2", incident_date="2026-11-09", location="Hall", contact_name="User3", contact_info="user3@example.com", status="Resolved")
            db.session.add_all([found, lost1, lost2])
            db.session.commit()

            # Insert two conflicting approved claims for the SAME found report
            claim1 = Claim(lost_report_id=lost1.id, found_report_id=found.id, claimant_id=aarav.id, claim_details="Proof 1", status="Approved", approved_found_report_id=None)
            claim2 = Claim(lost_report_id=lost2.id, found_report_id=found.id, claimant_id=user3.id, claim_details="Proof 2", status="Approved", approved_found_report_id=None)
            db.session.add_all([claim1, claim2])
            db.session.commit()
            c1_id, c2_id, f_id = claim1.id, claim2.id, found.id

            # Migration must stop safely and raise RuntimeError identifying conflicting found report ID
            with self.assertRaises(RuntimeError) as ctx:
                run_schema_migrations()

            self.assertIn(str(f_id), str(ctx.exception))
            self.assertIn("Multiple approved claims detected", str(ctx.exception))

            # Test requirement: Existing records and statuses remain unchanged when duplicate detection fails
            c1_check = db.session.get(Claim, c1_id)
            c2_check = db.session.get(Claim, c2_id)
            f_check = db.session.get(Report, f_id)

            self.assertIsNotNone(c1_check)
            self.assertIsNotNone(c2_check)
            self.assertEqual(c1_check.status, "Approved")
            self.assertEqual(c2_check.status, "Approved")
            self.assertEqual(f_check.status, "Resolved")
            self.assertIsNone(c1_check.approved_found_report_id)
            self.assertIsNone(c2_check.approved_found_report_id)

            # Clean up the duplicate claim before subsequent tests
            db.session.delete(c2_check)
            db.session.commit()
            run_schema_migrations()

    def test_running_migration_twice_is_idempotent(self):
        """Verify running schema migrations twice does not alter result or cause duplicate changes."""
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()

            found = Report(user_id=priya.id, report_type="found", title="Idempotent Found", category="Electronics & Gadgets", description="Item", incident_date="2026-11-10", location="Hall", contact_name="Priya", contact_info="priya@example.com", status="Resolved")
            lost = Report(user_id=aarav.id, report_type="lost", title="Idempotent Lost", category="Electronics & Gadgets", description="Item", incident_date="2026-11-10", location="Hall", contact_name="Aarav", contact_info="aarav@example.com", status="Resolved")
            db.session.add_all([found, lost])
            db.session.commit()

            claim = Claim(lost_report_id=lost.id, found_report_id=found.id, claimant_id=aarav.id, claim_details="Proof", status="Approved", approved_found_report_id=None)
            db.session.add(claim)
            db.session.commit()
            c_id, f_id = claim.id, found.id

            # First run
            run_schema_migrations()
            db.session.expire_all()
            c1 = db.session.get(Claim, c_id)
            self.assertEqual(c1.approved_found_report_id, f_id)

            # Second run
            run_schema_migrations()
            db.session.expire_all()
            c2 = db.session.get(Claim, c_id)
            self.assertEqual(c2.approved_found_report_id, f_id)
            self.assertEqual(c2.status, "Approved")

    def test_unique_index_prevents_second_approval_after_migration(self):
        """Verify the unique index prevents a second approval for the same found report after migration."""
        from sqlalchemy.exc import IntegrityError
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()
            user3 = User(name="User Three", email="competing@example.com", password_hash=generate_password_hash("user123"), role="user")
            db.session.add(user3)
            db.session.commit()

            found = Report(user_id=priya.id, report_type="found", title="Unique Index Test Found", category="Electronics & Gadgets", description="Item", incident_date="2026-11-11", location="Hall", contact_name="Priya", contact_info="priya@example.com", status="Resolved")
            lost1 = Report(user_id=aarav.id, report_type="lost", title="Unique Lost 1", category="Electronics & Gadgets", description="Item 1", incident_date="2026-11-11", location="Hall", contact_name="Aarav", contact_info="aarav@example.com", status="Resolved")
            lost2 = Report(user_id=user3.id, report_type="lost", title="Unique Lost 2", category="Electronics & Gadgets", description="Item 2", incident_date="2026-11-11", location="Hall", contact_name="User3", contact_info="user3@example.com", status="Active")
            db.session.add_all([found, lost1, lost2])
            db.session.commit()

            claim1 = Claim(lost_report_id=lost1.id, found_report_id=found.id, claimant_id=aarav.id, claim_details="Proof 1", status="Approved", approved_found_report_id=found.id)
            claim2 = Claim(lost_report_id=lost2.id, found_report_id=found.id, claimant_id=user3.id, claim_details="Proof 2", status="Pending", approved_found_report_id=None)
            db.session.add_all([claim1, claim2])
            db.session.commit()

            # Ensure migrations and unique index are applied
            run_schema_migrations()

            # Attempting second approval for same found report violates unique constraint uq_claims_approved_found
            claim2.approved_found_report_id = found.id
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_approved_claim_with_incorrect_non_null_approved_found_report_id_halts_migration(self):
        """Verify Approved claim with approved_found_report_id != found_report_id halts migration with RuntimeError."""
        with app.app_context():
            with db.engine.connect() as conn:
                conn.execute(db.text("DROP INDEX IF EXISTS uq_claims_approved_found;"))
                conn.commit()

            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()

            found1 = Report(user_id=priya.id, report_type="found", title="Found 1", category="Electronics & Gadgets", description="Item 1", incident_date="2026-11-12", location="Hall", contact_name="Priya", contact_info="priya@example.com", status="Resolved")
            found2 = Report(user_id=priya.id, report_type="found", title="Found 2", category="Electronics & Gadgets", description="Item 2", incident_date="2026-11-12", location="Hall", contact_name="Priya", contact_info="priya@example.com", status="Resolved")
            lost = Report(user_id=aarav.id, report_type="lost", title="Lost 1", category="Electronics & Gadgets", description="Item 1", incident_date="2026-11-12", location="Hall", contact_name="Aarav", contact_info="aarav@example.com", status="Resolved")
            db.session.add_all([found1, found2, lost])
            db.session.commit()

            claim = Claim(lost_report_id=lost.id, found_report_id=found1.id, claimant_id=aarav.id, claim_details="Proof", status="Approved", approved_found_report_id=found2.id)
            db.session.add(claim)
            db.session.commit()
            c_id = claim.id

            with self.assertRaises(RuntimeError) as ctx:
                run_schema_migrations()

            self.assertIn("differing from found_report_id", str(ctx.exception))

            # Preserve all existing records and values (not overwritten or deleted)
            db.session.expire_all()
            c_check = db.session.get(Claim, c_id)
            self.assertEqual(c_check.approved_found_report_id, found2.id)
            self.assertEqual(c_check.status, "Approved")

            # Clean up
            db.session.delete(c_check)
            db.session.commit()
            run_schema_migrations()

    def test_rejected_claim_with_non_null_approved_found_report_id_halts_migration(self):
        """Verify Rejected claim with non-NULL approved_found_report_id halts migration with RuntimeError."""
        with app.app_context():
            with db.engine.connect() as conn:
                conn.execute(db.text("DROP INDEX IF EXISTS uq_claims_approved_found;"))
                conn.commit()

            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()

            found = Report(user_id=priya.id, report_type="found", title="Found Rejected", category="Electronics & Gadgets", description="Item", incident_date="2026-11-12", location="Hall", contact_name="Priya", contact_info="priya@example.com", status="Active")
            lost = Report(user_id=aarav.id, report_type="lost", title="Lost Rejected", category="Electronics & Gadgets", description="Item", incident_date="2026-11-12", location="Hall", contact_name="Aarav", contact_info="aarav@example.com", status="Active")
            db.session.add_all([found, lost])
            db.session.commit()

            claim = Claim(lost_report_id=lost.id, found_report_id=found.id, claimant_id=aarav.id, claim_details="Proof", status="Rejected", approved_found_report_id=found.id)
            db.session.add(claim)
            db.session.commit()
            c_id = claim.id

            with self.assertRaises(RuntimeError) as ctx:
                run_schema_migrations()

            self.assertIn("Non-approved claim(s) have non-NULL approved_found_report_id", str(ctx.exception))

            # Preserve all existing records and values (not changed to NULL)
            db.session.expire_all()
            c_check = db.session.get(Claim, c_id)
            self.assertEqual(c_check.approved_found_report_id, found.id)
            self.assertEqual(c_check.status, "Rejected")

            # Clean up
            db.session.delete(c_check)
            db.session.commit()
            run_schema_migrations()

    def test_pending_claim_with_non_null_approved_found_report_id_halts_migration(self):
        """Verify Pending claim with non-NULL approved_found_report_id halts migration with RuntimeError."""
        with app.app_context():
            with db.engine.connect() as conn:
                conn.execute(db.text("DROP INDEX IF EXISTS uq_claims_approved_found;"))
                conn.commit()

            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()

            found = Report(user_id=priya.id, report_type="found", title="Found Pending", category="Electronics & Gadgets", description="Item", incident_date="2026-11-12", location="Hall", contact_name="Priya", contact_info="priya@example.com", status="Active")
            lost = Report(user_id=aarav.id, report_type="lost", title="Lost Pending", category="Electronics & Gadgets", description="Item", incident_date="2026-11-12", location="Hall", contact_name="Aarav", contact_info="aarav@example.com", status="Active")
            db.session.add_all([found, lost])
            db.session.commit()

            claim = Claim(lost_report_id=lost.id, found_report_id=found.id, claimant_id=aarav.id, claim_details="Proof", status="Pending", approved_found_report_id=found.id)
            db.session.add(claim)
            db.session.commit()
            c_id = claim.id

            with self.assertRaises(RuntimeError) as ctx:
                run_schema_migrations()

            self.assertIn("Non-approved claim(s) have non-NULL approved_found_report_id", str(ctx.exception))

            # Preserve all existing records and values (not changed to NULL)
            db.session.expire_all()
            c_check = db.session.get(Claim, c_id)
            self.assertEqual(c_check.approved_found_report_id, found.id)
            self.assertEqual(c_check.status, "Pending")

            # Clean up
            db.session.delete(c_check)
            db.session.commit()
            run_schema_migrations()

    def test_failed_validation_preserves_all_existing_records_and_values(self):
        """Verify that when migration validation fails, no records or historical NULL values are changed or backfilled."""
        with app.app_context():
            with db.engine.connect() as conn:
                conn.execute(db.text("DROP INDEX IF EXISTS uq_claims_approved_found;"))
                conn.commit()

            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()

            found1 = Report(user_id=priya.id, report_type="found", title="Batch Found 1", category="Electronics & Gadgets", description="Item 1", incident_date="2026-11-12", location="Hall", contact_name="Priya", contact_info="priya@example.com", status="Resolved")
            lost1 = Report(user_id=aarav.id, report_type="lost", title="Batch Lost 1", category="Electronics & Gadgets", description="Item 1", incident_date="2026-11-12", location="Hall", contact_name="Aarav", contact_info="aarav@example.com", status="Resolved")
            found2 = Report(user_id=priya.id, report_type="found", title="Batch Found 2", category="Wallets & Purses", description="Item 2", incident_date="2026-11-12", location="Cafe", contact_name="Priya", contact_info="priya@example.com", status="Active")
            lost2 = Report(user_id=aarav.id, report_type="lost", title="Batch Lost 2", category="Wallets & Purses", description="Item 2", incident_date="2026-11-12", location="Cafe", contact_name="Aarav", contact_info="aarav@example.com", status="Active")
            db.session.add_all([found1, lost1, found2, lost2])
            db.session.commit()

            # 1. Valid approved claim with NULL (should NOT be backfilled because batch fails)
            valid_approved = Claim(lost_report_id=lost1.id, found_report_id=found1.id, claimant_id=aarav.id, claim_details="Proof", status="Approved", approved_found_report_id=None)
            # 2. Invalid pending claim with non-NULL (causes migration failure)
            invalid_pending = Claim(lost_report_id=lost2.id, found_report_id=found2.id, claimant_id=aarav.id, claim_details="Proof", status="Pending", approved_found_report_id=found2.id)
            db.session.add_all([valid_approved, invalid_pending])
            db.session.commit()
            val_id, inv_id = valid_approved.id, invalid_pending.id

            with self.assertRaises(RuntimeError):
                run_schema_migrations()

            # Verify that atomic rollback occurred: valid_approved was NOT backfilled
            db.session.expire_all()
            val_check = db.session.get(Claim, val_id)
            inv_check = db.session.get(Claim, inv_id)

            self.assertIsNone(val_check.approved_found_report_id)
            self.assertEqual(val_check.status, "Approved")
            self.assertEqual(inv_check.approved_found_report_id, found2.id)
            self.assertEqual(inv_check.status, "Pending")

            # Clean up
            db.session.delete(inv_check)
            db.session.delete(val_check)
            db.session.commit()
            run_schema_migrations()

    def test_successful_migration_still_backfills_historical_null_values(self):
        """Verify successful migration properly backfills historical NULL values on approved claims while leaving others NULL."""
        with app.app_context():
            aarav = User.query.filter_by(email="aarav@example.com").first()
            priya = User.query.filter_by(email="priya@example.com").first()

            found1 = Report(user_id=priya.id, report_type="found", title="Hist Found 1", category="Electronics & Gadgets", description="Item 1", incident_date="2026-11-13", location="Hall", contact_name="Priya", contact_info="priya@example.com", status="Resolved")
            lost1 = Report(user_id=aarav.id, report_type="lost", title="Hist Lost 1", category="Electronics & Gadgets", description="Item 1", incident_date="2026-11-13", location="Hall", contact_name="Aarav", contact_info="aarav@example.com", status="Resolved")
            found2 = Report(user_id=priya.id, report_type="found", title="Hist Found 2", category="Wallets & Purses", description="Item 2", incident_date="2026-11-13", location="Cafe", contact_name="Priya", contact_info="priya@example.com", status="Active")
            lost2 = Report(user_id=aarav.id, report_type="lost", title="Hist Lost 2", category="Wallets & Purses", description="Item 2", incident_date="2026-11-13", location="Cafe", contact_name="Aarav", contact_info="aarav@example.com", status="Active")
            db.session.add_all([found1, lost1, found2, lost2])
            db.session.commit()

            app_claim = Claim(lost_report_id=lost1.id, found_report_id=found1.id, claimant_id=aarav.id, claim_details="Proof 1", status="Approved", approved_found_report_id=None)
            pend_claim = Claim(lost_report_id=lost2.id, found_report_id=found2.id, claimant_id=aarav.id, claim_details="Proof 2", status="Pending", approved_found_report_id=None)
            rej_claim = Claim(lost_report_id=lost2.id, found_report_id=found1.id, claimant_id=aarav.id, claim_details="Proof 3", status="Rejected", approved_found_report_id=None)
            db.session.add_all([app_claim, pend_claim, rej_claim])
            db.session.commit()
            ac_id, pc_id, rc_id, f1_id = app_claim.id, pend_claim.id, rej_claim.id, found1.id

            run_schema_migrations()
            db.session.expire_all()

            ac_check = db.session.get(Claim, ac_id)
            pc_check = db.session.get(Claim, pc_id)
            rc_check = db.session.get(Claim, rc_id)

            self.assertEqual(ac_check.approved_found_report_id, f1_id)
            self.assertEqual(ac_check.status, "Approved")
            self.assertIsNone(pc_check.approved_found_report_id)
            self.assertEqual(pc_check.status, "Pending")
            self.assertIsNone(rc_check.approved_found_report_id)
            self.assertEqual(rc_check.status, "Rejected")

    def test_debug_path_endpoint_returns_404(self):
        """Verify /debug-path route has been completely removed and returns HTTP 404."""
        response = self.client.get("/debug-path")
        self.assertEqual(response.status_code, 404)
        rule_paths = [rule.rule for rule in app.url_map.iter_rules()]
        self.assertNotIn("/debug-path", rule_paths)

    def test_500_response_does_not_expose_traceback_or_exception_details(self):
        """Verify the 500 error handler returns the generic error page and suppresses raw exceptions and tracebacks."""
        import app as app_module
        from werkzeug.exceptions import InternalServerError

        with app.app_context():
            # Test direct handler response with sensitive exception details
            sensitive_error = InternalServerError("Database failure at mysql://root:super_secret_pw@db.internal:3306/db with path /srv/secure/data")
            response_html, status_code = internal_error(sensitive_error)

            self.assertEqual(status_code, 500)
            self.assertIn("500 Internal Server Error", response_html)
            self.assertIn("Something went wrong", response_html)
            self.assertIn("<a href='/'>return to the registry portal</a>", response_html)
            # Ensure no credentials, tracebacks, or secret paths are returned to the client
            self.assertNotIn("super_secret_pw", response_html)
            self.assertNotIn("mysql://", response_html)
            self.assertNotIn("/srv/secure/data", response_html)
            self.assertNotIn("Traceback", response_html)

            # Test that production logging suppresses raw exceptions and tracebacks
            captured_output = io.StringIO()
            old_stdout = sys.stdout
            try:
                sys.stdout = captured_output
                orig_vercel = os.environ.get("VERCEL")
                os.environ["VERCEL"] = "1"
                try:
                    internal_error(sensitive_error)
                finally:
                    if orig_vercel is not None:
                        os.environ["VERCEL"] = orig_vercel
                    else:
                        os.environ.pop("VERCEL", None)
            finally:
                sys.stdout = old_stdout

            log_text = captured_output.getvalue()
            self.assertIn("500 Internal Server Error", log_text)
            self.assertNotIn("super_secret_pw", log_text)
            self.assertNotIn("Traceback", log_text)
            self.assertNotIn("/srv/secure/data", log_text)

    def test_500_error_handler_rolls_back_database_session(self):
        """Verify that the 500 error handler automatically rolls back uncommitted changes on db.session."""
        with app.app_context():
            # Create a dirty object in the database session without committing
            rollback_test_user = User(
                name="Rollback Test User",
                email="rollback_test@example.com",
                password_hash="fakehash",
                role="user"
            )
            db.session.add(rollback_test_user)
            self.assertIn(rollback_test_user, db.session.new)

            # Invoke 500 error handler
            from werkzeug.exceptions import InternalServerError
            with unittest.mock.patch("app.db.session.rollback", wraps=db.session.rollback) as mock_rollback:
                response_html, status = internal_error(InternalServerError("Simulated 500 failure"))
                self.assertEqual(status, 500)
                mock_rollback.assert_called_once()

            # Verify uncommitted changes are rolled back and not present in DB
            found_user = User.query.filter_by(email="rollback_test@example.com").first()
            self.assertIsNone(found_user)

    def test_production_configuration_disables_debug_mode(self):
        """Verify that production environments strictly disable debug mode, while local development retains it."""
        import app as app_module

        # 1. Verify production environment flags force debug mode to False
        for env_var in ["VERCEL", "FLASK_ENV", "ENVIRONMENT", "NODE_ENV"]:
            orig_val = os.environ.get(env_var)
            try:
                os.environ[env_var] = "production" if env_var != "VERCEL" else "1"
                self.assertFalse(get_debug_mode(), f"Expected get_debug_mode() to be False when {env_var} is set")
            finally:
                if orig_val is not None:
                    os.environ[env_var] = orig_val
                else:
                    os.environ.pop(env_var, None)

        # 2. Verify is_production_env configuration explicitly forces debug mode to False even if FLASK_DEBUG is enabled
        orig_prod = app_module.is_production_env
        orig_flask_debug = os.environ.get("FLASK_DEBUG")
        try:
            app_module.is_production_env = True
            os.environ["FLASK_DEBUG"] = "true"
            self.assertFalse(get_debug_mode(), "Production configuration must disable debug mode even if FLASK_DEBUG=true")
        finally:
            app_module.is_production_env = orig_prod
            if orig_flask_debug is not None:
                os.environ["FLASK_DEBUG"] = orig_flask_debug
            else:
                os.environ.pop("FLASK_DEBUG", None)

        # 3. Verify local development (non-production) retains debug mode
        try:
            app_module.is_production_env = False
            prod_vars = ["VERCEL", "FLASK_ENV", "ENVIRONMENT", "NODE_ENV"]
            saved_vars = {v: os.environ.pop(v, None) for v in prod_vars}
            os.environ.pop("FLASK_DEBUG", None)
            try:
                self.assertTrue(get_debug_mode(), "Expected local development environment to retain debug mode")
            finally:
                for v, val in saved_vars.items():
                    if val is not None:
                        os.environ[v] = val
        finally:
            app_module.is_production_env = orig_prod

if __name__ == "__main__":
    unittest.main()
