#!/bin/bash

if [ "$#" -ne 3 ]; then
    echo "Usage: $0 <container_name_X> <lines_Y> <search_string_Z>"
    exit 1
fi

CONTAINER_X="$1"
LINES_Y="$2"
SEARCH_Z="$3"

if docker logs --tail "$LINES_Y" "$CONTAINER_X" 2>&1 | grep -Fq "$SEARCH_Z"; then
    echo "true"
    exit 0
else
    echo "false"
    exit 1
fi