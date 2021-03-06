#!/bin/bash
set -x

###################################################################
# This script will get executed *once* the Docker container has 
# been built. Commands that need to be executed with all available
# tools and the filesystem mount enabled should be located here. 
###################################################################

cd /app 

make generate-version-file

# Upgrade schema of the notification_api database.
flask db upgrade

# Bubble up the main Docker command to container.
exec "$@"
