#!/usr/bin/env python3
import requests
import argparse
import re
import os
import time
import csv
from dotenv import load_dotenv

# ----------------------------
# Load .env
# ----------------------------
load_dotenv()

CANVAS_BASE_URL = os.getenv("CANVAS_URL")
ACCESS_TOKEN = os.getenv("CANVAS_TOKEN")

if not CANVAS_BASE_URL or not ACCESS_TOKEN:
    raise RuntimeError("Missing CANVAS_URL or CANVAS_TOKEN in .env file")

HEADERS = {"Authorization": f"Bearer {ACCESS_TOKEN}"}

ERROR_LOG = []   # NEW — store error rows here


# ----------------------------
# Pagination helper
# ----------------------------
def get_next_link(link_header):
    if not link_header:
        return None
    for part in link_header.split(","):
        m = re.match(r'\s*<([^>]+)>;\s*rel="next"', part)
        if m:
            return m.group(1)
    return None


# ----------------------------
# Fetch all enrollments
# ----------------------------
def get_all_enrollments(course_id):
    url = f"{CANVAS_BASE_URL}/courses/{course_id}/enrollments?per_page=100"
    results = []

    while url:
        resp = requests.get(url, headers=HEADERS)
        resp.raise_for_status()
        results.extend(resp.json())
        url = get_next_link(resp.headers.get("Link"))

    return results


# ----------------------------
# Check if already enrolled
# ----------------------------
def is_already_enrolled(enrollments_target, user_id):
    return any(e["user_id"] == user_id for e in enrollments_target)


# ----------------------------
# Enroll user (retry + log errors to CSV)
# ----------------------------
def enroll_user(target_course_id, user, enrollment_type, dry_run=False):
    user_id = user["id"]
    username = user.get("name", "Unknown Name")
    email = user.get("login_id") or user.get("email") or "No Email"

    url = f"{CANVAS_BASE_URL}/courses/{target_course_id}/enrollments"

    payload = {
        "enrollment": {
            "user_id": user_id,
            "type": enrollment_type,
            "enrollment_state": "active",
            "notify": False,
        }
    }

    print(f"→ Enrolling {username} ({email}) [{user_id}] as {enrollment_type}...", end=" ")

    if dry_run:
        print("[DRY RUN]")
        return True

    # ---- Try 1 ----
    try:
        resp = requests.post(url, headers=HEADERS, json=payload)
        resp.raise_for_status()
        print("OK")
        return True

    except requests.exceptions.HTTPError as err:
        print(f"\n   ERROR {resp.status_code}: {err}")
        print(f"   User: {username} ({email})")
        print("   Retrying...")

        # LOG ERROR (first attempt) — NEW
        ERROR_LOG.append({
            "user_id": user_id,
            "name": username,
            "email": email,
            "enrollment_type": enrollment_type,
            "status": resp.status_code,
            "error_message": resp.text.strip().replace("\n", " "),
            "attempt": "first"
        })

    time.sleep(0.5)

    # ---- Try 2 ----
    try:
        resp = requests.post(url, headers=HEADERS, json=payload)
        resp.raise_for_status()
        print("OK (retry)")
        return True

    except requests.exceptions.HTTPError as err:
        print(f"\n   FAILED AGAIN: {resp.status_code}: {resp.text[:300]}")
        print(f"   User: {username} ({email})")
        print("   Skipping.\n")

        # LOG ERROR (second/final attempt) — NEW
        ERROR_LOG.append({
            "user_id": user_id,
            "name": username,
            "email": email,
            "enrollment_type": enrollment_type,
            "status": resp.status_code,
            "error_message": resp.text.strip().replace("\n", " "),
            "attempt": "retry"
        })

        return False


# ----------------------------
# Write errors.csv at end  — NEW
# ----------------------------
def write_error_csv():
    if not ERROR_LOG:
        print("No errors to write to CSV.")
        return

    filename = "enrollment_errors.csv"
    print(f"\nWriting {len(ERROR_LOG)} errors to {filename}...")

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "user_id",
                "name",
                "email",
                "enrollment_type",
                "status",
                "error_message",
                "attempt",
            ],
        )
        writer.writeheader()
        writer.writerows(ERROR_LOG)

    print(f"Done. Error log saved as {filename}\n")


# ----------------------------
# Main
# ----------------------------
def main():
    parser = argparse.ArgumentParser(description="Clone enrollments between Canvas courses.")
    parser.add_argument("source_course")
    parser.add_argument("target_course")
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    print(f"Fetching enrollments from source course {args.source_course}...")
    source_enrollments = get_all_enrollments(args.source_course)
    print(f"Found {len(source_enrollments)} enrollments.\n")

    print(f"Fetching enrollments from target course {args.target_course}...")
    target_enrollments = get_all_enrollments(args.target_course)
    print(f"Target already has {len(target_enrollments)} enrollments.\n")

    count_ok = 0
    count_skip_exists = 0
    count_failed = 0

    for e in source_enrollments:
        user = e["user"]
        user_id = user["id"]
        enrollment_type = e.get("type", "StudentEnrollment")

        if is_already_enrolled(target_enrollments, user_id):
            print(f"→ Skipping {user['name']} ({user.get('email','')}) – already enrolled")
            count_skip_exists += 1
            continue

        success = enroll_user(
            args.target_course,
            user,
            enrollment_type,
            dry_run=args.dry_run,
        )

        if success:
            count_ok += 1
        else:
            count_failed += 1

    # Write error CSV every run
    write_error_csv()

    print("\n=== SUMMARY ===")
    print(f"Successful enrollments:     {count_ok}")
    print(f"Skipped (already exists):   {count_skip_exists}")
    print(f"Failed (after retry):       {count_failed}")
    print(f"Dry run:                    {args.dry_run}")


if __name__ == "__main__":
    main()
