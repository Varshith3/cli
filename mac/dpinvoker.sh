#!/bin/bash

if [[ $1 = "aws" ]]
then
    echo "Testing"
   . $DP_LOCAL_SETUP_PATH_MAC/generateToken.sh $@
elif [[ $1 = "aws_ca" ]]
then
   . $DP_LOCAL_SETUP_PATH_MAC/codeartifact.sh $@
else
   echo "Unknown command $1, valid commands are aws and aws_ca"
fi