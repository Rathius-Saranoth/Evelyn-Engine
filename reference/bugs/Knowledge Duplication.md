# Bug - Knowledge Duplication

Reported: 2026-03-16

Knowledge entries are being duplicated in the collection; when checks were added to the sync script, it was done by checking the mtime but also the file hash.  However this leaves duplicates in the collection since it is not detecting by file name. It should at the least check for duplicates by full directory and replace with the newest file.