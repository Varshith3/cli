#!/bin/bash

installBrew() {
  brew --version

  if [ $? -eq 127 ]
  then
    echo "Home brew not present in the system!!"
    echo "Installing Home brew!!"
    ./installBrew.sh
    echo "Home brew installed!!"
  else
    echo "Home brew is already present in this system"
  fi
}

installPyENV() {
  pyenv --version

  if [ $? -eq 127 ]
  then
    echo "pyenv is not present in the system!!"
    echo "Installing pyenv!!"
    brew update
    brew install pyenv

    echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.zshrc
    echo 'command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.zshrc
    echo 'eval "$(pyenv init -)"' >> ~/.zshrc

    echo "pyenv installed!!"
  else
    echo "pyenv is already present in this system"
  fi

  pyenv global 3.8.10
  if [ $? -eq 0 ]
  then
    echo "Python 3.8.10 is already present in the system!!"
  else
    echo "Installing python 3.8.10!!"
    pyenv install 3.8.10
    pyenv global 3.8.10
    pyenv rehash
    pip install pipenv
    pyenv rehash
    echo "python 3.8.10 installed!!"
  fi
}

installAWS() {
  aws --version

  if [ $? -eq 127 ]
  then
    echo "AWS CLI is not present in the system!!"
    echo "Installing AWS CLI!!"
    brew install awscli
  else
    echo "AWS CLI is already present in this system"
  fi
}

installSAML2AWS() {
  saml2aws --version

  if [ $? -eq 127 ]
  then
    echo "SAML 2 AWS is not present in the system!!"
    echo "Installing SAML 2 AWS!!"
    brew install saml2aws
  else
    echo "SAML 2 AWS is already present in this system"
  fi
}

installJQ() {
  jq --version

  if [ $? -eq 127 ]
  then
    echo "JQ is not present in the system!!"
    echo "Installing JQ!!"
    brew install jq
  else
    echo "JQ is already present in this system"
  fi
}

generateSamlConf() {
  for acc in $(jq '. | keys | .[]' ../roles/data_platform_roles.json); do
      for role in $(jq -r ".[$acc].roles[].name" ../roles/data_platform_roles.json); do
  echo "[$acc-$role]"
  echo Name                = ${acc}-$role
  echo app_id              =
  echo url                 = $(jq -r ".[$acc].okta_url" ../roles/data_platform_roles.json)
  echo username            =
  echo provider            = Okta
  echo mfa                 = Auto
  echo skip_verify         = false
  echo timeout             = 0
  echo aws_urn             = urn:amazon:webservices
  echo aws_session_duration= 3600
  echo aws_profile         = $acc-$role
  echo resource_id         =
  echo subdomain           =
  echo role_arn            = arn:aws:iam::$(jq -r ".[$acc].account_id" ../roles/data_platform_roles.json):role/$role
  echo region              =
  echo http_attempts_count =
  echo http_retry_delay    =
  echo credentials_file    =
  echo saml_cache          = false
  echo
      done
  done > ~/.saml2aws


  sed -i '' 's/\"//g' ~/.saml2aws
}

installBrew
installPyENV
installAWS
installSAML2AWS
installJQ
generateSamlConf

export DP_LOCAL_SETUP_PATH_MAC=`pwd`
touch ~/.bash_profile
touch ~/.bashrc
touch ~/.zshrc
if grep "dpinvoker.sh" ~/.bash_profile
then
      echo "shortcut dp already created"
else
  echo "alias dp='. `pwd`/dpinvoker.sh'" >> ~/.bash_profile
fi
source ~/.bash_profile
if grep ".bash_profile" ~/.bashrc
then
      echo "bashrc already updated"
else
  echo ". ~/.bash_profile" >> ~/.bashrc
  echo ". ~/.bash_profile" >> ~/.zshrc
  echo "export DP_LOCAL_SETUP_PATH_MAC=`pwd`" >> ~/.bashrc
  echo "export DP_LOCAL_SETUP_PATH_MAC=`pwd`" >> ~/.zshrc
  echo "export PATH=$PATH:~/go/bin" >> ~/.bashrc
  echo "export PATH=$PATH:~/go/bin" >> ~/.zshrc
fi

export PATH=$PATH:~/go/bin