# notices

What the desk sends the Administrator, as files you can edit. `prompts/` is what the seats are fed; this is
what the person at the desk is sent: the automated system messages that land in the Inbox. One folder per
sender; one file per notice.

`board/` is the Board's paper: the office above the Administrator's. Every file in it is sent to every desk
once: a fresh desk gets them all on its first read, and a file added later is sent to existing desks the next
time they look. The record of what was sent when is `board.json` in the app's state dir, by number; the text is
read from the file every time, so an edit here shows on every desk at once (a notice already dismissed stays
dismissed; dismissal is by number). Files go in name order; the first is dated newest, so it sits on top of the
tray and is the one that opens.

A file is front matter, then the sheet:

```
---
number: BN-001            the stable id; dismissal and the sent record key on it
title: Welcome aboard.    the one line the tray shows
status: WELCOME           the stamp
hot: false                red stamp when true
---
## A section heading
A paragraph is one line of the sheet.

- [Settings](nav:settings) An action row: the lead in the margin, the whole row a link. `$steps` counts these.
A line can also link [in the running text](nav:home), anywhere in it, as many times as it needs.
`a line all in backticks is set in mono`
**a line all in bold is set as a warning**
> a quoted line
```

`$steps` in the title or a line is the number of action rows in the file, as a word ("three"), so a checklist
counts itself and stays right when you add or remove an item.

Links: `nav:home`, `nav:settings`, `nav:settings/<section>` (`signature`, `tasks`, `notifications`, `storage`,
`sharing`), `nav:providers` open those surfaces; `brief:<project id>` opens that brief;
`<kind>:<number>` opens another document in the tray. The Deputy's notices are not files here: the engine
files them at the moment they happen (`mizpah.notices`).
