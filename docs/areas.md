# Areas of a monorepo

A monorepo holds several products, and the people who answer for one are not the people who answer for the next. embry0 learns a repository's parts from a file in the repository, `.embry0/areas.yml`, and names the part in everything it says about the work there: the proposals it asks owners to answer, the pull requests it opens, and the jobs. A repository without the file works as before. The design is in [the spec](superpowers/specs/2026-10-25-monorepo-areas-design.md).

## The file

`.embry0/areas.yml`, on the repository's default branch:

```yaml
areas:
  - name: Billing
    paths:
      - "services/billing/**"
      - "services/billing-app/**"
      - "web/apps/billing/"
    logs: ["billing-api"]
    owners: ["octo"]
  - name: Search
    paths: ["services/search/**", "web/apps/search/"]
    logs: ["search-api"]
```

| Key | Means |
|---|---|
| `name` | What embry0 calls the area: `[Billing]` in a pull request title, the area on a job or a proposal. Its slug (`billing`) names the label. Required; an entry without one is skipped. |
| `paths` | Globs over the repository's files: `**` any number of directories, `*` and `?` within one, a trailing `/` everything below. The first area whose glob matches a file has it. |
| `logs` | Strings that, found in a watcher case's log signature or title, put the case in the area. Case does not matter; the name of the container or service the area runs as is the usual choice. |
| `owners` | GitHub logins asked first about the area's work (a leading `@` is fine). Leave it out and the repository's owners are asked. |

embry0 reads the file with the repository's organization credential and keeps it an hour, so a change shows within the hour. A file that is missing or not YAML names no areas.

## Where the area shows

- **Owner proposals.** A context finding is in the area of its file, a watcher case in the area its log signature or title names, an issue in the area one of its labels names (`Billing`, `billing` or `area:billing`). The area's owners are asked; an area without owners asks the repository's ([owner proposals](api.md#owner-proposals)). The Owner proposals page shows the area next to the repository, and the feed's `area` field lets a chat assistant name it in its message.
- **Pull requests embry0 opens.** On GitHub, embry0 reads the files the branch changes from its base: the area most of them fall in labels the pull request `area:<slug>` and starts its title with `[Name]`, unless a conventional scope already names it (`fix(billing): ...`). A change that falls in no area opens as before.
- **Jobs.** The job records the area of the pull request it opened (`area` on `GET /jobs` and `GET /jobs/{id}`), shown before the job's title on the Jobs list and next to the repository on the job page.
