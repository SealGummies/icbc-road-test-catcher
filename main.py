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
_icbc_email_base_seq = 0


def appointment_key(appt):
    return (appt["appointmentDt"]["date"], appt.get("startTm", "99:99"))


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


def get_available_appointments(limit=None):
    """Return available slots in desired range, sorted earliest first.

    limit: if set, return at most this many candidates.
    """
    global current_token

    if not current_token:
        if not refresh_token():
            return []

    try:
        candidates = []
        desired_start = datetime.strptime(CONFIG["desired_date_range"]["start"], "%Y-%m-%d").date()
        desired_end = datetime.strptime(CONFIG["desired_date_range"]["end"], "%Y-%m-%d").date()

        from datetime import timedelta
        now_vancouver = datetime.now(pytz.timezone(CONFIG['timezone']))
        tomorrow = (now_vancouver + timedelta(days=1)).date()
        effective_start = max(desired_start, tomorrow)

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
                      f"Filtering for {effective_start} – {desired_end}", flush=True)

                in_range = 0
                for appointment in appointments:
                    if "appointmentDt" in appointment:
                        appointment_date = datetime.strptime(appointment["appointmentDt"]["date"], "%Y-%m-%d").date()
                        if effective_start <= appointment_date <= desired_end:
                            in_range += 1
                            candidates.append(appointment)
                print(f"  → {in_range} slot(s) within desired range", flush=True)

        candidates.sort(key=appointment_key)
        if limit:
            candidates = candidates[:limit]
        return candidates

    except Exception as e:
        print(f"Error checking available dates: {e}")
        current_token = None
        return []


def get_earliest_appointment():
    slots = get_available_appointments(limit=1)
    return slots[0] if slots else None


def lock_appointment(appointment, is_reschedule=False):
    """Lock a slot. With is_reschedule=True, skips the driver-refresh/cancel steps
    so an existing booking is preserved while we hold the new slot temporarily."""
    global current_token, drvr_id, login_data_full

    if not current_token or not drvr_id:
        if not refresh_token():
            return None

    try:
        booked_ts = datetime.now(pytz.timezone(CONFIG['timezone'])).strftime("%Y-%m-%dT%H:%M:%S")

        unlock_data = {"appointmentDt": {}, "dlExam": {}, "drvrDriver": {"drvrId": drvr_id}, "drscDrvSchl": {}}

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

        booking_headers = {
            "Content-Type": "application/json",
            "Authorization": current_token,
            "Origin": "https://onlinebusiness.icbc.com",
            "Referer": "https://onlinebusiness.icbc.com/webdeas-ui/booking",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/116.0.0.0"
        }
        driver_headers = {**booking_headers, "Referer": "https://onlinebusiness.icbc.com/webdeas-ui/driver"}

        with httpx.Client() as client:
            if is_reschedule:
                # Mirror browser reschedule sequence: refresh driver state first,
                # then clear any temp lock, then lock new slot — WITHOUT cancelling existing booking.
                r = client.put(
                    CONFIG["driver_url"],
                    json={"drvrLastName": CONFIG["credentials"]["drvrLastName"],
                          "licenceNumber": CONFIG["credentials"]["licenceNumber"]},
                    headers=driver_headers
                )
                print(f"[reschedule] driver refresh: {r.status_code} {r.text[:200]}", flush=True)

            print(f"Sending unlock request...", flush=True)
            response = client.put(CONFIG["lock_url"], json=unlock_data, headers=booking_headers)
            print(f"Unlock response {response.status_code}: {response.text[:200]}", flush=True)
            if not response.is_success:
                response.raise_for_status()

            time.sleep(2)

            target = appointment['appointmentDt']['date']
            print(f"Sending lock request for {target} {appointment.get('startTm','')}...", flush=True)
            response = client.put(CONFIG["lock_url"], json=lock_data, headers=booking_headers)
            print(f"Lock response {response.status_code}: {response.text[:500]}", flush=True)
            if not response.is_success:
                response.raise_for_status()

            resulting_timezone = response.json()
            print(f"Lock succeeded for {target}. bookedTs={resulting_timezone.get('bookedTs')}", flush=True)
            return resulting_timezone["bookedTs"]

    except Exception as e:
        print(f"Error locking appointment: {e}", flush=True)
        return None


def send_otp_email(booked_ts):
    global current_token, drvr_id, _icbc_email_base_seq

    mail = None
    try:
        mail = imaplib.IMAP4_SSL(CONFIG["gmail"]["imap_server"])
        mail.login(CONFIG["gmail"]["email"], CONFIG["gmail"]["password"])
        mail.select("inbox")
        status, messages = mail.search(None, 'FROM "roadtests-donotreply@icbc.com"')
        if status == "OK" and messages[0]:
            seq_nums = messages[0].split()
            _icbc_email_base_seq = int(seq_nums[-1])
            print(f"[otp] IMAP base seq recorded: {_icbc_email_base_seq} ({len(seq_nums)} existing ICBC email(s))", flush=True)
        else:
            _icbc_email_base_seq = 0
            print("[otp] No existing ICBC emails; base seq = 0", flush=True)
    except imaplib.IMAP4.error as e:
        print(f"[otp] IMAP login/search failed while recording base seq: {e}", flush=True)
        print("[otp] WARNING: stale OTP isolation unavailable — proceeding anyway", flush=True)
    except Exception as e:
        print(f"[otp] Unexpected error recording base seq: {e}", flush=True)
    finally:
        if mail:
            try:
                mail.logout()
            except Exception:
                pass

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

            print(f"[otp] sendOTP response: {response.status_code} {response.text[:300]}", flush=True)
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


def get_otp_from_email():
    global _icbc_email_base_seq
    mail = None
    try:
        mail = imaplib.IMAP4_SSL(CONFIG["gmail"]["imap_server"])
        try:
            mail.login(CONFIG["gmail"]["email"], CONFIG["gmail"]["password"])
        except imaplib.IMAP4.error as e:
            print(f"[otp] IMAP login failed: {e}", flush=True)
            return None
        mail.select("inbox")

        status, messages = mail.search(None, 'FROM "roadtests-donotreply@icbc.com"')
        if status != "OK":
            print("[otp] IMAP search failed (status not OK)", flush=True)
            return None

        message_ids = messages[0].split()
        new_ids = [mid for mid in message_ids if int(mid) > _icbc_email_base_seq]
        if not new_ids:
            print(f"No new emails from ICBC (base seq={_icbc_email_base_seq}, total={len(message_ids)})", flush=True)
            return None

        latest_email_id = new_ids[-1]
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

            print(f"[otp] verifyOTP response: {response.status_code} {response.text[:300]}", flush=True)
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

            print(f"[book] book response: {response.status_code} {response.text[:500]}", flush=True)
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
            r = client.put(
                CONFIG["driver_url"],
                json={"drvrLastName": CONFIG["credentials"]["drvrLastName"], "licenceNumber": CONFIG["credentials"]["licenceNumber"]},
                headers=headers
            )
            print(f"[cancel] driver refresh: {r.status_code} {r.text[:200]}", flush=True)

            # Step 2: unlock any existing lock
            r = client.put(
                CONFIG["lock_url"],
                json={"appointmentDt": {}, "dlExam": {}, "drvrDriver": {"drvrId": drvr_id}, "drscDrvSchl": {}},
                headers=headers
            )
            print(f"[cancel] unlock: {r.status_code} {r.text[:200]}", flush=True)

            # Step 3: cancel (no OTP needed)
            response = client.put(CONFIG["cancel_url"], json=cancel_data, headers=headers)
            print(f"[cancel] cancel response: {response.status_code} {response.text[:500]}", flush=True)
            if not response.is_success:
                response.raise_for_status()

            print(f"Existing appointment on {date_str} cancelled successfully", flush=True)
            return True

    except Exception as e:
        print(f"Error cancelling appointment: {e}", flush=True)
        return False


def _complete_booking(appointment, booked_ts=None):
    """OTP-verify and book a slot. If booked_ts is provided the slot is already locked."""
    if booked_ts is None:
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
    candidates = get_available_appointments(limit=5)
    if not candidates:
        print("No suitable dates available for booking")
        return False

    appointment = candidates[0]
    new_date = datetime.strptime(appointment["appointmentDt"]["date"], "%Y-%m-%d").date()
    new_time = appointment.get("startTm", "")
    print(f"Earliest available slot: {new_date} {new_time}", flush=True)

    existing = get_existing_appointment()
    if existing:
        existing_date = datetime.strptime(existing["appointmentDt"]["date"], "%Y-%m-%d").date()
        existing_time = existing.get("startTm", "")
        print(f"Existing appointment: {existing_date} {existing_time}", flush=True)
        if appointment_key(appointment) >= appointment_key(existing):
            print(f"Available slot {new_date} {new_time} is not earlier than existing {existing_date} {existing_time}, skipping", flush=True)
            return False

        print(f"Found earlier slot {new_date} {new_time} vs existing {existing_date} {existing_time}", flush=True)

        # Reschedule flow: lock new slot FIRST while keeping existing booking safe.
        # The existing appointment is only cancelled after we confirm the new lock.
        # If the new slot is already taken, we never lose the existing booking.
        result = False
        for i, candidate in enumerate(candidates):
            candidate_date = candidate["appointmentDt"]["date"]
            candidate_time = candidate.get("startTm", "")
            if i > 0:
                print(f"Trying next candidate: {candidate_date} {candidate_time}", flush=True)

            print(f"Attempting reschedule lock for {candidate_date} {candidate_time} "
                  f"(existing appointment preserved until lock confirmed)...", flush=True)
            booked_ts = lock_appointment(candidate, is_reschedule=True)

            if booked_ts:
                print(f"Lock confirmed. Now cancelling existing appointment on "
                      f"{existing_date} {existing_time}...", flush=True)
                if not cancel_appointment(existing):
                    print("Warning: failed to cancel existing appointment; "
                          "proceeding anyway — server may handle it atomically.", flush=True)
                refresh_token()
                result = _complete_booking(candidate, booked_ts=booked_ts)
                if result:
                    break
                print(f"Booking failed after successful lock for {candidate_date} {candidate_time}.", flush=True)
            else:
                print(f"Lock failed for {candidate_date} {candidate_time} — slot likely taken. "
                      f"Existing appointment on {existing_date} {existing_time} is still safe.", flush=True)

        refresh_token()
        return result

    # No existing appointment — just try candidates in order.
    result = False
    for i, candidate in enumerate(candidates):
        candidate_date = candidate["appointmentDt"]["date"]
        candidate_time = candidate.get("startTm", "")
        if i > 0:
            print(f"Retrying with next candidate: {candidate_date} {candidate_time}", flush=True)
        result = _complete_booking(candidate)
        if result:
            break
        print(f"Failed to book {candidate_date} {candidate_time}, trying next candidate...", flush=True)

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
                if auto_book_earliest_appointment():
                    print("Earlier slot successfully booked. Stopping.", flush=True)
                    return
                last_check_time = current_time

            time.sleep(1)

    except KeyboardInterrupt:
        print("\nScript stopped by user")


if __name__ == "__main__":
    main()