# Terraform TFE Team Membership Remover

CLI utility to find users by email in a Terraform Cloud/Enterprise organization, verify membership in a specified team, and remove all matching users from that team in a single bulk API request.

## What it does

Given an organization name, a team name, and one or more user emails, the script:

1. Looks up each email via the organization-memberships API and collects:
	 - organization-membership id (ou-...)
	 - user id (user-...)
	 - all team ids the user belongs to
2. Lists all teams in the organization and locates the team by its name (e.g., "owners"). Collects team metadata: users-count and visibility.
3. For each email, checks whether the user belongs to the specified team.
4. For all matching users, sends a single bulk DELETE request to remove their organization-memberships from the team.
5. Waits 3 seconds and prints a success line per removed user (status 204), or an error summary if the bulk request fails.

## API endpoints used

- Search a user by email (query parameter):
	- GET `${TFE_HOST}/api/v2/organizations/{org}/organization-memberships?q=<email>`
- List teams in an organization:
	- GET `${TFE_HOST}/api/v2/organizations/{org}/teams`
- Bulk remove users (by organization-membership ids) from a team:
	- DELETE `${TFE_HOST}/api/v2/teams/{team_id}/relationships/organization-memberships`
	- Payload:
		```json
		{ "data": [ { "type": "organization-memberships", "id": "ou-..." } ] }
		```

## Requirements

- Python 3.8+
- Environment variables:
	- `TFE_HOST` (required), e.g. `https://app.terraform.io` or your TFE base URL
	- `TFE_TOKEN` (required) — a token with permissions to read memberships/teams and remove members

## Installation

Clone this repo in a dev container or local environment with Python available.

## Usage

Basic (single email):

```bash
export TFE_HOST="https://app.terraform.io"        # or your TFE URL
export TFE_TOKEN="<your-api-token>"

python main.py --org prod-tdmund-tf --team owners --email user1@example.com
```

Multiple emails (repeat flag or space-separated):

```bash
python main.py --org prod-tdmund-tf --team owners \
	--email user1@example.com --email user2@example.com

python main.py --org prod-tdmund-tf --team owners \
	--email user1@example.com user2@example.com user3@gmail.com
```

Comma-separated (quote to avoid shell splitting), also supported:

```bash
python main.py --org prod-tdmund-tf --team owners --email 'user1@example.com, user2@example.com'
```

From a file (must be UTF-8 .txt; comments with `#` are ignored; commas or newlines are accepted):

```bash
python main.py --org prod-tdmund-tf --team owners --emails-file /path/to/emails.txt
```

From stdin:

```bash
echo -e "user1@example.com\nuser2@example.com" | \
	python main.py --org prod-tdmund-tf --team owners --emails-file -
```

The script merges emails from flags and file, trims whitespace, removes duplicates (preserving order), and validates the file is UTF-8 text (rejects binary files) with a `.txt` extension (when using a path).

## Output and exit codes

- Per-email, the script prints whether the user exists, whether they’re a member of the specified team, and the team’s users-count and visibility.
- If one or more users are members, the script performs a single bulk removal and prints a success line per removed user.

Exit codes:

- `0`: All processed; removals (if any) succeeded.
- `1`: At least one email wasn’t found in the organization.
- `2`: Team name not found in the organization.
- `3`: At least one user exists but is not a member of the specified team.
- `4`: Bulk removal request failed.

## Notes

- The script requires `TFE_HOST` and `TFE_TOKEN` to be set; it exits early with a clear error if either is missing.
- The bulk removal waits 3 seconds and expects HTTP 204 on success.
- No dry-run option is present by default. If you want one, open an issue or request and it can be added.

## Security

- Keep your `TFE_TOKEN` secret. Prefer passing it via environment variable and avoid committing it to source control.