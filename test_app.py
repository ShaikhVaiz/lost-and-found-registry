import unittest
import os
import io

os.environ["DISABLE_SQLALCHEMY_CEXT"] = "1"

from werkzeug.security import generate_password_hash
from app import app, db, User, Report, PasswordReset, calculate_match_score

class DigitalLostAndFoundTestSuite(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()

        with app.app_context():
            # Clean up test user if exists
            test_u = User.query.filter_by(email="teststudent@example.com").first()
            if test_u:
                db.session.delete(test_u)
                db.session.commit()

            # Ensure seed user aarav has known password user123
            aarav = User.query.filter_by(email="aarav@example.com").first()
            if aarav:
                aarav.password_hash = generate_password_hash("user123")
                db.session.commit()

    def tearDown(self):
        with app.app_context():
            Report.query.delete()
            aarav = User.query.filter_by(email="aarav@example.com").first()
            if aarav:
                aarav.password_hash = generate_password_hash("user123")
            db.session.commit()

    def login_citizen(self):
        return self.client.post("/login", data={
            "email": "aarav@example.com",
            "password": "user123"
        }, follow_redirects=True)

    def login_admin(self):
        return self.client.post("/login", data={
            "login_id": "admin",
            "password": "admin123"
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

if __name__ == "__main__":
    unittest.main()
