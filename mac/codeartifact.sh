#!/bin/bash

if [ $# -ne 2 ]
then
  echo "please pass refresh_token"
  return
fi

echo "Checking if pip or pip3"
if command -v pip &>/dev/null; then
    pip_cmd="pip"
    echo "You have pip!"
    echo "Using pip."
elif command -v pip3 &>/dev/null; then
    pip_cmd="pip3"
    echo "You have pip3!"
    echo "Using pip3."
else
    echo "Error: Neither 'pip' nor 'pip3' command found. Please install pip or pip3."
    exit 1
fi

echo "Generating codeartifact auth token for snapshot repo."
CODEARTIFACT_AUTH_TOKEN=$(aws codeartifact get-authorization-token --domain dpp-dp-tools-repositories-domain --domain-owner 626645654318 --region us-west-2 --query authorizationToken --duration-seconds 3600 --output text)
if ! command -v aws &>/dev/null; then
    echo "Error: 'aws' command not found. Please install the AWS CLI."
    exit 1
fi

# Adding token to
"$pip_cmd" config set global.index-url https://aws:"$CODEARTIFACT_AUTH_TOKEN"@dpp-dp-tools-repositories-domain-626645654318.d.codeartifact.us-west-2.amazonaws.com/pypi/dpnp-tools-python-snapshot-local/simple/

echo "CodeArtifact token refresh success."