# Bot Contributor Notes

This repo successfully produced a commit that GitHub attributed to
`opencode-agent[bot]` in the contributors graph by changing the repo-local git
author identity before committing and pushing.

## What worked

Use repo-local git config, not global config:

```bash
cd /home/dinkum/projects/gitmem

git config --local user.name 'opencode-agent[bot]'
git config --local user.email 'opencode-agent[bot]@users.noreply.github.com'
git config --local commit.gpgsign false

git add -A
git commit -m "your message"
git push origin HEAD:main
```

The important parts were:

- The commit author/committer name matched the bot account name.
- The commit email matched the bot noreply address.
- The commit landed on the default branch.
- The commit was not empty.

## Restore normal identity

After the bot-attributed push, remove the repo-local override so the repo goes
back to the normal inherited git identity:

```bash
cd /home/dinkum/projects/gitmem

git config --local --unset-all user.name
git config --local --unset-all user.email
git config --local --unset-all commit.gpgsign
```

Verify the active identity:

```bash
git var GIT_AUTHOR_IDENT
```

## Template for other bot contributors

Replace `SOME-BOT` with the exact GitHub bot account name:

```bash
git config --local user.name 'SOME-BOT[bot]'
git config --local user.email 'SOME-BOT[bot]@users.noreply.github.com'
git config --local commit.gpgsign false
```

Examples:

- `dependabot[bot]` / `dependabot[bot]@users.noreply.github.com`
- `opencode-agent[bot]` / `opencode-agent[bot]@users.noreply.github.com`

## Caveats

- This is commit metadata attribution, not true authentication as the GitHub
  App.
- It worked for `opencode-agent[bot]`, but other bots may or may not be mapped
  by GitHub the same way.
- If you need guaranteed app-authored activity, use a GitHub App installation
  token in CI or a backend job and commit/push from there.
- Avoid leaving the bot identity configured in a repo longer than needed.

## References

- GitHub commit email attribution:
  https://docs.github.com/articles/setting-your-commit-email-address-in-git
- GitHub contributors graph rules:
  https://docs.github.com/en/repositories/viewing-activity-and-data-for-your-repository/viewing-a-projects-contributors
