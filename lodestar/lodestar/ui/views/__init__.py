"""One module per page.

Named `views/` rather than `pages/` deliberately: Streamlit auto-discovers a
directory literally named `pages` next to the entry script and turns it into
its own sidebar navigation. That would put a second, competing nav in the app
AND link to modules that cannot run standalone — every render() here needs
`db_path` and `run` passed in by the router.

Kept separate rather than gathered into a single large app file so that each
page can be read, changed, and broken in isolation — and so the router in
app.py stays a router. Every module exposes one function:

    render(*, db_path: str, run: dict) -> None

`run` is the selected run's summary dict; `db_path` points at the runs
database. Neither is a live connection: every read goes through ui/data.py,
which caches on plain hashable arguments.
"""
