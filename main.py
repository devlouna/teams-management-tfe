#!/usr/bin/env python3


"""
Remove a user (by email) from a Terraform Cloud / Enterprise team.

Requires:
 - TFE_TOKEN environment variable (API token)
 - TFE_HOST optional (default: https://app.terraform.io)

Usage:
 python remove_user_from_team.py --org my-org --team "Team Name" --email user@example.com [--dry-run]
"""
import os
import sys
import argparse
import requests
import logging

logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)


TFE_HOST = os.environ.get("TFE_HOST", "https://app.terraform.io")
API_BASE = f"{TFE_HOST.rstrip('/')}/api/v2"
TOKEN    = os.environ.get("TFE_TOKEN")

if not TOKEN:
    print("Error: TFE_TOKEN environment variable is required.", file=sys.stderr)
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/vnd.api+json",
    "Accept": "application/vnd.api+json",
}


### step 1: function used to get all teams in the org
def get_org_teams(org):
    """Return list of teams in the organization."""
    url = f"{API_BASE}/organizations/{org}/teams"
    teams = []
    while url:
        resp = requests.get(url, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
        teams.extend(data.get("data", []))
        url = data.get("links", {}).get("next")
    return teams

### step 2: function to find user and collect all their team IDs
def find_user_and_team(org, email):
    """Search organization-memberships by email and collect identifiers for next steps.

    Steps:
    - Call GET /organizations/{org}/organization-memberships?q=<email>
      (URL-encode the email and use it directly as the value of the 'q' param)
    - If a matching organization-membership is found, extract and return:
        * org_membership_id (membership resource id)
        * user_id (from relationships.user.data.id)
        * team_ids (list of team ids from relationships.teams.data[].id)

    Returns a tuple (org_membership_id, user_id, team_ids) or (None, None, [])
    """
    import urllib.parse

    # Retrieve user data by quering organization-memberships with email
    q      = urllib.parse.quote_plus(email)
    url    = f"{API_BASE}/organizations/{org}/organization-memberships?q={q}"
    resp   = requests.get(url, headers=HEADERS)
    resp.raise_for_status()
    data   = resp.json()
    items  = data.get("data", [])
    if not items:
        return (None, None, [])

    # Assume first match is the user we want
    m                 = items[0]
    org_membership_id = m.get("id")
    user_id           = m.get("relationships", {}).get("user", {}).get("data", {}).get("id")
    team_entries      = m.get("relationships", {}).get("teams", {}).get("data", []) or []
    team_ids          = [t.get("id") for t in team_entries if t.get("id")]

    return (org_membership_id, user_id, team_ids)


def remove_org_memberships_from_team(team_id, org_membership_ids, team_name):
    """Bulk remove organization-memberships from a team using relationships endpoint.

    Endpoint: DELETE /api/v2/teams/{team_id}/relationships/organization-memberships
    Payload: { "data": [ {"type":"organization-memberships","id":"ou-..."}, ... ] }

    Returns (success: bool, status_code: int, response_text: str)
    """
    if not org_membership_ids:
        return (False, 400, "No organization-membership IDs to remove")

    url     = f"{API_BASE}/teams/{team_id}/relationships/organization-memberships"
    payload = {
        "data": [{"type": "organization-memberships", "id": oid} for oid in org_membership_ids]
    }

    try:
        resp = requests.delete(url, headers=HEADERS, json=payload)
    except Exception as e:
        return (False, 0, str(e))

    # Wait 3 seconds after the request as requested
    try:
        import time
        time.sleep(3)
    except Exception:
        pass

    if resp.status_code == 204:
        return (True, resp.status_code, "")
    return (False, resp.status_code, getattr(resp, "text", ""))


def main():
    """CLI: find user(s), validate target team by name, and check membership for each email.

    Emails can be provided multiple times (--email a --email b) or as a comma-separated list
    (--email a,b or --emails a,b)."""
    p = argparse.ArgumentParser(description="Find user(s) by email, validate team by name, and check membership")
    p.add_argument("--org", required=True, help="Organization name")
    p.add_argument(
        "--email", "--emails", dest="emails", required=True, action="append", nargs="+",
        help=(
            "Email(s) to search. Supports: repeat flag (--email a --email b), "
            "space-separated (--email a b), or comma-separated (--email 'a,b')."
        ),
    )
    p.add_argument("--team", required=True, help="Team name to validate and check membership")
    args = p.parse_args()

    # Normalize emails: support repeated flags and comma-separated lists
    # args.emails is a list of lists due to action=append + nargs="+"
    tokens = []
    for group in args.emails:
        tokens.extend(group)

    emails = []
    for token in tokens:
        # split commas inside tokens, strip whitespace; allow trailing commas
        parts = [p.strip() for p in str(token).split(',') if p and p.strip()]
        emails.extend(parts)

    if not emails:
        print("No valid emails provided.")
        sys.exit(1)

    # Step 2: find the team by name in org teams once
    teams               = get_org_teams(args.org)
    target_team         = None
    for t in teams:
        if (t.get("attributes", {}) or {}).get("name") == args.team:
            target_team = t
            break

    if not target_team:
        print(f"Team '{args.team}' not found in organization '{args.org}'.")
        sys.exit(2)

    team_id                  = target_team.get("id")
    attrs                    = target_team.get("attributes", {}) or {}
    users_count              = attrs.get("users-count")
    visibility               = attrs.get("visibility")

    overall_status           = 0
    membership_ids_to_remove = []  # Collect valid org_membership_ids for removal in one request
    email_by_membership_id   = {}

    print("\n ************* Processing Users details and retrieving their TFE data ****************")
    # Process each email
    for email in emails:
        logger.info(f"\n♻️️️️️️ Processing email: {email}")

        # Step 1: find user and collect their team ids
        org_membership_id, user_id, user_team_ids = find_user_and_team(args.org, email)

        if not org_membership_id:
            logger.error(f"  ❌ User with email '{email}' not found in organization '{args.org}'.")
            overall_status = max(overall_status, 1)
            continue

        # Step 3: check whether user belongs to the specified team
        if team_id in set(user_team_ids):
            logger.info(
                "\n  User belongs to team:\n"
                f" - email: {email}\n"
                f" - user_id: {user_id}\n"
                f" - org_membership_id: {org_membership_id}\n"
                f" - team_name: {args.team}\n"
                f" - team_id: {team_id}\n"
                f" - users-count: {users_count}\n"
                f" - visibility: {visibility}"
            )
            # queue for bulk removal
            membership_ids_to_remove.append(org_membership_id)
            email_by_membership_id[org_membership_id] = email
        else:
            logger.info(
                f"\n User and team exist, but user is not a member of the specified team '{args.team}':\n"
                f" - email: {email}\n"
                f" - user_id: {user_id}\n"
                f" - org_membership_id: {org_membership_id}\n"
                f" - team_name: {args.team}\n"
                f" - team_id: {team_id}\n"
                f" - users-count: {users_count}\n"
                f" - visibility: {visibility}"
            )
            overall_status = max(overall_status, 3)

    # Perform a single bulk delete request for all queued users
    print("\n ************* Starting removal process for all users from TFE ****************")
    if membership_ids_to_remove:
        ok, status_code, resp_text = remove_org_memberships_from_team(team_id, membership_ids_to_remove, args.team)
        if ok:
            for oid in membership_ids_to_remove:
                email = email_by_membership_id.get(oid, "<unknown>")
                logger.info(
                    f"✅ User '{email}' with organization-membership '{oid}' successfully removed from team '{args.team}'."
                )
        else:
            overall_status = max(overall_status, 4)
            logger.error(
                "Failed to remove users from team in bulk request:\n"
                f" - team_name: {args.team}\n"
                f" - team_id: {team_id}\n"
                f" - status_code: {status_code}\n"
                f" - response: {resp_text}"
            )
    else:
        logger.info(f"\n 🌝 No validated users to remove from the team {args.team}...")

    sys.exit(overall_status)


if __name__ == "__main__":
    main()

