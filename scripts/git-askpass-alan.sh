#!/bin/sh
# GIT_ASKPASS helper for alan's transport_env choke point (alan/secrets_store.py).
# Answers whichever prompt git passes as $1 (username or password) with the
# token named by ALAN_GIT_ASKPASS_TOKEN. The token is never in argv (git only
# ever sees this script's path) and never written to a git config file.
exec printf '%s\n' "${ALAN_GIT_ASKPASS_TOKEN:?ALAN_GIT_ASKPASS_TOKEN not set}"
