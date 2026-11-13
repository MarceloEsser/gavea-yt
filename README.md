# YouTube community posts -> Atom feed (GitHub Actions)

A scheduled GitHub Action scrapes the Posts tab of the channels in
`channels.txt` and commits `feed.xml`. Subscribe to that file in Capy Reader.

## Setup
1. Create a new **public** GitHub repo and upload everything in this folder
   (keep the `.github/workflows/` path exactly).
2. Repo -> Settings -> Actions -> General -> Workflow permissions ->
   **Read and write permissions** -> Save.
3. Actions tab -> "Scrape YouTube posts" -> **Run workflow** (first manual run).
4. Open the run's log. You want to see `UC...: N posts on page, N new`.
5. In Capy Reader, add the feed URL:
   `https://raw.githubusercontent.com/<user>/<repo>/main/feed.xml`
   (raw.githubusercontent.com caches for a few minutes, so updates are not instant.)

## Adding channels
Add a line to `channels.txt` with a channel ID (UC...) or an @handle.

## If it breaks
- Red X on the run + `consent`/`no ytInitialData` error: YouTube is blocking
  or redirecting GitHub's runner. Nothing in the repo can fully fix that.
- `page parsed but no posts found`: either the channel has no posts, or
  YouTube changed its page format. Paste the log and the parser can be adjusted.
- GitHub disables scheduled workflows in a public repo after 60 days with no
  repository activity. The workflow commits whenever new posts appear, which
  should count, but if posts stop coming, re-enable it in the Actions tab.
