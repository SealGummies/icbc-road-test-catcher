from datetime import datetime
import imaplib
import httpx
import email
import time
import pytz
import re
import os

CONFIG = {
    "login_url": "https://onlinebusiness.icbc.com/deas-api/v1/webLogin/webLogin",
    "appointments_url": "https://onlinebusiness.icbc.com/deas-api/v1/web/getAvailableAppointments",
    "lock_url": "https://onlinebusiness.icbc.com/deas-api/v1/web/lock",
    "send_otp_url": "https://onlinebusiness.icbc.com/deas-api/v1/web/sendOTP",
    "verify_otp_url": "https://onlinebusiness.icbc.com/deas-api/v1/web/verifyOTP",
    "book_url": "https://onlinebusiness.icbc.com/deas-api/v1/web/book",
    "cancel_url": "https://onlinebusiness.icbc.com/deas-api/v1/web/cancel",
    "driver_url": "https://onlinebusiness.icbc.com/deas-api/v1/web/driver",

    "credentials": {
        "drvrLastName": os.getenv("USER_LAST_NAME"),
        "licenceNumber": os.getenv("USER_LICENSE_NUMBER"),
        "keyword": os.getenv("USER_KEYWORD")
    },

    "appointment_request_base": {
        "examType": os.getenv("EXAM_TYPE", "7-R-1"),
        "examDate": datetime.now().strftime("%Y-%m-%d"),
        "prfDaysOfWeek": "[0,1,2,3,4,5,6]",
        "prfPartsOfDay": "[0,1]",
        "lastName": os.getenv("USER_LAST_NAME"),
        "licenseNumber": os.getenv("USER_LICENSE_NUMBER")
    },

    "location_ids": [int(x) for x in os.getenv("LOCATION_IDS", "274").split(",")],

    "gmail": {
        "email": os.getenv("USER_GMAIL"),
        "password": os.getenv("USER_GMAIL_APP_PASSWORD"),
        "imap_server": "imap.gmail.com"
    },

    "desired_date_range": {
        "start": os.getenv("DESIRED_DATE_START", "2025-06-24"),
        "end": os.getenv("DESIRED_DATE_END", "2025-06-30")
    },

    "timezone": "America/Vancouver",
    "check_interval": 90,
    "token_refresh_interval": 1500
}

current_token = None
last_token_refresh = None
drvr_id = None
login_data_full = None


def validate_config():
    """Validate that all required environment variables are set"""
    required_vars = [
        "USER_LAST_NAME",
        "USER_LICENSE_NUMBER", 
        "USER_KEYWORD",
        "USER_GMAIL",
        "USER_GMAIL_APP_PASSWORD"
    ]
    
    optional_vars = ["DESIRED_DATE_START", "DESIRED_DATE_END"]
    
    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        print(f"Error: Missing required environment variables: {', '.join(missing_vars)}")
        print("Please set these variables in your .env file or environment")
        return False
    
    return True


def refresh_token():
    global current_token, last_token_refresh, drvr_id, login_data_full
    try:
        with httpx.Client() as client:
            response = client.put(
                CONFIG["login_url"],
                json=CONFIG["credentials"],
                headers={
                    "Content-Type": "application/json",
                    "Origin": "https://onlinebusiness.icbc.com",
                    "Referer": "https://onlinebusiness.icbc.com/webdeas-ui/",
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/116.0.0.0"
                }
            )
            response.raise_for_status()

            auth_header = response.headers.get('Authorization')
            if auth_header and auth_header.startswith('Bearer '):
                current_token = auth_header
                last_token_refresh = datetime.now(pytz.timezone(CONFIG['timezone']))

                try:
                    login_data_full = response.json()
                    drvr_id = login_data_full.get('drvrId')
                    eligible = login_data_full.get('eligibleExams', [])
                    if eligible:
                        exam_code = eligible[0]["code"]
                        CONFIG["appointment_request_base"]["examType"] = exam_code
                        print(f"Token refreshed. drvrID: {drvr_id}, exam type: {exam_code}", flush=True)
                    else:
                        print(f"Token refreshed. drvrID: {drvr_id}, WARNING: no eligible exams found", flush=True)
                except Exception as e:
                    print(f"Failed to get drvrID from response: {e}")

                return True

        print("Failed to get token from headers")
        return False
    except Exception as e:
        print(f"Error refreshing token: {e}")
        return False


def get_earliest_appointment():
    global current_token

    if not current_token:
        if not refresh_token():
            return None

    try:
        earliest_appointment = None
        desired_start = datetime.strptime(CONFIG["desired_date_range"]["start"], "%Y-%m-%d").date()
        desired_end = datetime.strptime(CONFIG["desired_date_range"]["end"], "%Y-%m-%d").date()

        with httpx.Client() as client:
            for location_id in CONFIG["location_ids"]:
                request_data = CONFIG["appointment_request_base"].copy()
                request_data["examDate"] = datetime.now().strftime("%Y-%m-%d")
                request_data["aPosID"] = location_id

                response = client.post(
                    CONFIG["appointments_url"],
                    json=request_data,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": current_token,
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/116.0.0.0"
                    }
                )
                response.raise_for_status()

                appointments = response.json()
                print(f"Location {location_id}: {len(appointments)} slots returned by API. "
                      f"Filtering for {desired_start} – {desired_end}", flush=True)

                from datetime import timedelta
                now_vancouver = datetime.now(pytz.timezone(CONFIG['timezone']))
                tomorrow = (now_vancouver + timedelta(days=1)).date()
                effective_start = max(desired_start, tomorrow)
                in_range = 0
                for appointment in appointments:
                    if "appointmentDt" in appointment:
                        appointment_date = datetime.strptime(appointment["appointmentDt"]["date"], "%Y-%m-%d").date()

                        if effective_start <= appointment_date <= desired_end:
                            in_range += 1
                            if (earliest_appointment is None or
                                    appointment_date < datetime.strptime(earliest_appointment["appointmentDt"]["date"],
                                                                         "%Y-%m-%d").date()):
                                earliest_appointment = appointment
                print(f"  → {in_range} slot(s) within desired range", flush=True)

        return earliest_appointment

    except Exception as e:
        print(f"Error checking available dates: {e}")
        current_token = None
        return None


def lock_appointment(appointment):
    global current_token, drvr_id, login_data_full

    if not current_token or not drvr_id:
        if not refresh_token():
            return None

    try:
        booked_ts = datetime.now(pytz.timezone(CONFIG['timezone'])).strftime("%Y-%m-%dT%H:%M:%S")

        unlock_data = {"appointmentDt": {}, "dlExam": {}, "drvrDriver": {"drvrId": drvr_id}, "drscDrvSchl": {}}

        # Build drvrDriver using full login response if available
        drvr_driver = login_data_full.get("drvrDriver", {"drvrId": drvr_id}) if login_data_full else {"drvrId": drvr_id}
        if "drvrId" not in drvr_driver:
            drvr_driver["drvrId"] = drvr_id

        lock_data = {
            "appointmentDt": appointment["appointmentDt"],
            "dlExam": appointment["dlExam"],
            "examType": appointment["dlExam"]["code"],
            "drvrDriver": drvr_driver,
            "drscDrvSchl": {},
            "instructorDlNum": None,
            "bookedTs": booked_ts,
            "startTm": appointment["startTm"],
            "endTm": appointment["endTm"],
            "posId": appointment["posId"],
            "resourceId": appointment["resourceId"],
            "signature": appointment["signature"]
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": current_token,
            "Origin": "https://onlinebusiness.icbc.com",
            "Referer": "https://onlinebusiness.icbc.com/webdeas-ui/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/116.0.0.0"
        }

        with httpx.Client() as client:
            print(f"Sending unlock request...", flush=True)
            response = client.put(CONFIG["lock_url"], json=unlock_data, headers=headers)
            print(f"Unlock response {response.status_code}: {response.text}", flush=True)
            if not response.is_success:
                response.raise_for_status()

            time.sleep(10)

            print(f"Sending lock request for {appointment['appointmentDt']['date']}...", flush=True)
            response = client.put(CONFIG["lock_url"], json=lock_data, headers=headers)
            if not response.is_success:
                print(f"Lock step failed {response.status_code}: {response.text}", flush=True)
                response.raise_for_status()

            resulting_timezone = response.json()
            print(f"Date {appointment['appointmentDt']['date']} successfully locked", flush=True)
            return resulting_timezone["bookedTs"]

    except Exception as e:
        print(f"Error locking appointment: {e}", flush=True)
        return None


def send_otp_email(booked_ts):
    global current_token, drvr_id

    clear_old_icbc_emails()

    try:
        otp_data = {
            "bookedTs": booked_ts,
            "drvrID": drvr_id,
            "method": "E"
        }

        timeout = httpx.Timeout(15.0, read=None)
        with httpx.Client() as client:
            response = client.post(
                CONFIG["send_otp_url"],
                json=otp_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": current_token,
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/116.0.0.0"
                },
                timeout=timeout
            )

            response.raise_for_status()

            result = response.json()
            if result.get("code") == "success":
                print("OTP code sent to email")
                return True
            else:
                print("Failed to send OTP code")
                return False

    except Exception as e:
        print(f"Error sending OTP code: {e}")
        return False


def clear_old_icbc_emails():
    """Mark all existing unread ICBC emails as read to avoid picking up stale OTPs."""
    mail = None
    try:
        mail = imaplib.IMAP4_SSL(CONFIG["gmail"]["imap_server"])
        mail.login(CONFIG["gmail"]["email"], CONFIG["gmail"]["password"])
        mail.select("inbox")
        status, messages = mail.search(None, '(UNSEEN FROM "roadtests-donotreply@icbc.com")')
        if status == "OK" and messages[0]:
            for msg_id in messages[0].split():
                mail.store(msg_id, '+FLAGS', '\\Seen')
    except Exception:
        pass
    finally:
        if mail:
            mail.logout()


def get_otp_from_email():
    mail = None
    try:
        mail = imaplib.IMAP4_SSL(CONFIG["gmail"]["imap_server"])
        mail.login(CONFIG["gmail"]["email"], CONFIG["gmail"]["password"])
        mail.select("inbox")

        status, messages = mail.search(None, '(UNSEEN FROM "roadtests-donotreply@icbc.com")')
        if status != "OK":
            print("Failed to find emails from ICBC")
            return None

        message_ids = messages[0].split()
        if not message_ids:
            print("No new emails from ICBC")
            return None

        latest_email_id = message_ids[-1]
        status, msg_data = mail.fetch(latest_email_id, "(RFC822)")
        if status != "OK":
            print("Failed to read email")
            return None

        raw_email = msg_data[0][1]
        email_message = email.message_from_bytes(raw_email)

        for part in email_message.walk():
            if part.get_content_type() == "text/html":
                html_content = part.get_payload(decode=True).decode()
                match = re.search(r'<h2[^>]*>(\d{6})</h2>', html_content)
                if match:
                    return match.group(1)

        print("Failed to find OTP code in email")
        return None

    except Exception as e:
        print(f"Error getting OTP code from email: {e}")
        return None
    finally:
        if mail:
            mail.logout()


def verify_otp(booked_ts, otp_code):
    global current_token, drvr_id

    try:
        verify_data = {
            "bookedTs": booked_ts,
            "drvrID": drvr_id,
            "code": otp_code
        }

        with httpx.Client() as client:
            response = client.put(
                CONFIG["verify_otp_url"],
                json=verify_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": current_token,
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/116.0.0.0"
                }
            )

            response.raise_for_status()

            result = response.json()
            if result.get("status") == "VERIFIED":
                print("OTP code successfully verified")
                return True
            else:
                print("Invalid OTP code")
                return False

    except Exception as e:
        print(f"Error verifying OTP code: {e}")
        return False


def book_appointment(booked_ts):
    global current_token, drvr_id

    try:
        book_data = {
            "userId": f"WEBD:{drvr_id}",
            "appointment": {
                "drvrDriver": {"drvrId": drvr_id}
            }
        }

        with httpx.Client() as client:
            response = client.put(
                CONFIG["book_url"],
                json=book_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": current_token,
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/116.0.0.0"
                }
            )

            response.raise_for_status()

            result = response.json()
            if result.get("code") == "success":
                print("Booking completed successfully!")
                return True
            else:
                print("Failed to complete booking")
                return False

    except Exception as e:
        print(f"Error completing booking: {e}")
        return False


def get_existing_appointment():
    """Returns current booked appointment from login data, or None."""
    if login_data_full:
        appointments = login_data_full.get('webAappointments', [])
        if appointments:
            return appointments[0]
    return None


def cancel_appointment(existing):
    """Cancel an existing booked appointment. Returns True on success."""
    global current_token, drvr_id

    date_str = existing.get("appointmentDt", {}).get("date", "unknown")
    print(f"Cancelling existing appointment on {date_str}...", flush=True)

    headers = {
        "Content-Type": "application/json",
        "Authorization": current_token,
        "Origin": "https://onlinebusiness.icbc.com",
        "Referer": "https://onlinebusiness.icbc.com/webdeas-ui/driver",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/116.0.0.0"
    }

    try:
        cancel_data = {
            "userId": f"WEBD:{drvr_id}",
            "instDlNum": None,
            "appointment": existing,
            "action": "CANCELLED",
            "appointmentDt": existing["appointmentDt"],
            "startTm": existing["startTm"],
            "remark": "Cancelled by user action through WebDEAS"
        }

        with httpx.Client() as client:
            # Step 1: refresh driver state (required before cancel)
            client.put(
                CONFIG["driver_url"],
                json={"drvrLastName": CONFIG["credentials"]["drvrLastName"], "licenceNumber": CONFIG["credentials"]["licenceNumber"]},
                headers=headers
            )

            # Step 2: unlock any existing lock
            client.put(
                CONFIG["lock_url"],
                json={"appointmentDt": {}, "dlExam": {}, "drvrDriver": {"drvrId": drvr_id}, "drscDrvSchl": {}},
                headers=headers
            )

            # Step 3: cancel (no OTP needed)
            response = client.put(CONFIG["cancel_url"], json=cancel_data, headers=headers)
            if not response.is_success:
                print(f"Cancel failed {response.status_code}: {response.text}", flush=True)
                response.raise_for_status()

            print(f"Existing appointment on {date_str} cancelled successfully", flush=True)
            return True

    except Exception as e:
        print(f"Error cancelling appointment: {e}", flush=True)
        return False


def _complete_booking(appointment):
    """Lock, send OTP, verify, and book a given appointment slot."""
    booked_ts = lock_appointment(appointment)
    if not booked_ts:
        return False

    if not send_otp_email(booked_ts):
        return False

    otp_code = None
    for _ in range(20):
        time.sleep(10)
        otp_code = get_otp_from_email()
        if otp_code:
            break

    if not otp_code:
        print("Failed to get OTP code from email", flush=True)
        return False

    if not verify_otp(booked_ts, otp_code):
        return False

    return book_appointment(booked_ts)


def auto_book_earliest_appointment():
    appointment = get_earliest_appointment()
    if not appointment:
        print("No suitable dates available for booking")
        return False

    new_date = datetime.strptime(appointment["appointmentDt"]["date"], "%Y-%m-%d").date()
    print(f"Earliest available slot: {new_date} {appointment['startTm']}", flush=True)

    existing = get_existing_appointment()
    if existing:
        existing_date = datetime.strptime(existing["appointmentDt"]["date"], "%Y-%m-%d").date()
        print(f"Existing appointment: {existing_date}", flush=True)
        if new_date >= existing_date:
            print(f"Available slot {new_date} is not earlier than existing {existing_date}, skipping", flush=True)
            return False
        print(f"Found earlier slot {new_date} vs existing {existing_date}, will cancel and rebook", flush=True)
        if not cancel_appointment(existing):
            print("Failed to cancel existing appointment, aborting to avoid losing it", flush=True)
            return False
        refresh_token()

    result = _complete_booking(appointment)
    refresh_token()
    return result


def main():
    if not validate_config():
        return
        
    if not refresh_token():
        print("Failed to get token. Check your credentials.")
        return

    last_check_time = time.time()
    last_token_time = time.time()

    print("Script started. Beginning monitoring for available dates...")

    try:
        while True:
            current_time = time.time()

            if current_time - last_token_time >= CONFIG["token_refresh_interval"]:
                if refresh_token():
                    last_token_time = current_time

            if current_time - last_check_time >= CONFIG["check_interval"]:
                auto_book_earliest_appointment()
                last_check_time = current_time

            time.sleep(1)

    except KeyboardInterrupt:
        print("\nScript stopped by user")


if __name__ == "__main__":
    main()