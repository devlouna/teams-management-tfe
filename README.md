# teams-management-tfe


1. Gather inputs: organization name, team name, user email (CLI args or env).

2. Call the organization memberships endpoint to retrieve org users and org teams.

3. Inspect each organization membership (response[].attributes) to find the user email.

4. If user found, list the specified team's members and check whether the user is a member.

5. If the user is a member of that team, call the team's membership DELETE endpoint to remove the