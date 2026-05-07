#!/bin/bash

if [ $# -ne 3 ]
then
  echo "please pass AWS account (ex: dpnp/dpp) and role(ex:data_engg/admin/vz_eng)"
  return
fi

account=$2
role=$(jq -r ".[\"$account\"].roles[] |select(.short_name==\"$3\") | .name" $DP_LOCAL_SETUP_PATH_MAC/../roles/data_platform_roles.json)
#role="user-role-dp-data-engineers"
echo "logging in as $account-$role"
export AWS_PROFILE="$account-$role"
export SAML2AWS_IDP_ACCOUNT="$account-$role"
saml2aws login --force
`saml2aws script`
#printf "\e[32mAccess_key = $AWS_ACCESS_KEY_ID\e[m\n"
#printf "\e[32mSecret_key = $AWS_SECRET_ACCESS_KEY\e[m\n"