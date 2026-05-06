# Evelyn Tool Cycle

I'm wondering if the log_context_fact tool is not getting used often because of it's docstring and the way tools are used per turn.

Right now, the docstring for logging a context fact includes a line about searching first. I think this is causing the model to think that it needs to search first, and then log the fact. But the model doesn't have the ability to do both in one turn. So it's not using the tool at all and is instead just searching the vault, which gets sent as the command, thus ending the turn.

Perhaps the docstring needs changed, or we can tackle this in the thinking process?
