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

### step 2: function to find user and matched team id
def find_user_and_team(org, email):
    """Search organization-memberships with a query for email, save ids, and verify team exists.

        Steps:
        - Call GET /organizations/{org}/organization-memberships?q=<email>
            (URL-encode the email and use it directly as the value of the 'q' param)
    - If a matching organization-membership is found, extract:
        * org_membership_id (membership resource id)
        * user_id (from relationships.user.data.id)
        * team_ids (from relationships.teams.data[].id)
    - Call GET /organizations/{org}/teams and check whether any team id from team_ids
      appears in the teams list. If found, print the user email, user id and team id.

    Returns a tuple (org_membership_id, user_id, matched_team_id) or (None, None, None)
    """
    import urllib.parse

    # Retrieve user data by quering organization-memberships with email
    q = urllib.parse.quote_plus(email)
    url = f"{API_BASE}/organizations/{org}/organization-memberships?q={q}"
    resp = requests.get(url, headers=HEADERS)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("data", [])
    if not items:
        return (None, None, None)

    # Assume first match is the user we want
    m = items[0]
    org_membership_id = m.get("id")
    user_id = m.get("relationships", {}).get("user", {}).get("data", {}).get("id")
    team_entries = m.get("relationships", {}).get("teams", {}).get("data", []) or []
    team_ids = [t.get("id") for t in team_entries if t.get("id")]

    # Get list of teams in the org and check for team id of the user
    teams = get_org_teams(org)
    teams_ids_in_org = {t.get("id") for t in teams if t.get("id")}

    matched_team_id = None
    for tid in team_ids:
        if tid in teams_ids_in_org:
            matched_team_id = tid
            break

    if matched_team_id:
        print(f"Found user {email} user_id={user_id} org_membership_id={org_membership_id} team_id={matched_team_id}")
        return (org_membership_id, user_id, matched_team_id)

    return (org_membership_id, user_id, None)


def main():
    """CLI entrypoint to look up a user via organization-memberships and report a matched team id."""
    p = argparse.ArgumentParser(description="Find a user and matched team using organization-memberships")
    p.add_argument("--org", required=True, help="Organization name")
    p.add_argument("--email", required=True, help="User email to search")
    args = p.parse_args()

    org_membership_id, user_id, matched_team_id = find_user_and_team(args.org, args.email)

    if not org_membership_id:
        print(f"User with email '{args.email}' not found in organization '{args.org}'.")
        sys.exit(1)

    if matched_team_id:
        # find_user_and_team already prints a success line; just exit 0
        sys.exit(0)
    else:
        print(
            f"Found user {args.email} user_id={user_id} org_membership_id={org_membership_id} but no matching team id found in teams list."
        )
        sys.exit(2)


if __name__ == "__main__":
    main()

