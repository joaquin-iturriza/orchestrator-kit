#!/bin/bash
# CLAUDE.md (Talking to me): the interactive question prompt ends the turn until the user replies, so it is refused here. A question goes in the
# message; only the item that needs the answer waits, everything else continues.
echo "no_blocking_question: AskUserQuestion is refused in this project (CLAUDE.md, Talking to me: never use AskUserQuestion). Put the question in your message, mark only that item as waiting, and continue with everything else." >&2
exit 2
