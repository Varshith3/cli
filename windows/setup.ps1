param (

    [Parameter(HelpMessage = "Command to execute")]
    [string]$command
)


$DOCUMENT_PATH = [Environment]::GetFolderPath("MyDocuments")
$WINDOWS_POWERSHELL_PATH = "$DOCUMENT_PATH\WindowsPowerShell"
$WINDOWS_MODULE_PATH = "$WINDOWS_POWERSHELL_PATH\Modules"
$SCRIPT_PARENT_PATH = (Split-Path -parent $PSCommandPath)

FUNCTION setup_dp_module
{
    PROCESS {

        $MODULE_PATH = "$SCRIPT_PARENT_PATH\dp"
        $ROLES_FILE_PATH = "$SCRIPT_PARENT_PATH\..\roles\data_platform_roles.json"

        Write-Output "Setting up the Module"
        Write-Output "From - Module Path:           '$MODULE_PATH'"
        Write-Output "To   - Powershell Module DIR: '$WINDOWS_MODULE_PATH'"

        New-Item -ItemType Directory -Force -Path $WINDOWS_MODULE_PATH  | Out-Null
        Copy-Item -Path $MODULE_PATH -Destination $WINDOWS_MODULE_PATH -Recurse -Force
        Import-Module dp -Force

        Copy-Item -Path $ROLES_FILE_PATH -Destination "$WINDOWS_MODULE_PATH\dp\" -Recurse -Force

    }
}

FUNCTION setup_profile
{
    PROCESS {
        $PROFILE_PATH = "$SCRIPT_PARENT_PATH\profiles"

        Write-debug "Setting up the Profiles"
        Write-Output "From: '$PROFILE_PATH'"

        Copy-Item -Path $PROFILE_PATH\profile.ps1 -Destination $WINDOWS_POWERSHELL_PATH\Profile.ps1 -Recurse -Force
#        Copy-Item -Path $PROFILE_PATH\.saml2aws   -Destination $env:USERPROFILE\.saml2aws -Recurse -Force
    }
}

Function read_user_data_file
{
    $dp_script_file = "$env:USERPROFILE/.dp_script"
    $original_file_data = ""
    if (Test-Path $dp_script_file -PathType Leaf)
    {
        $original_file_data = get-content $dp_script_file -raw
    }
    $original_file_data = $original_file_data -replace '\\', '\\'
    $original_file_data = convertfrom-stringdata($original_file_data)
    $original_file_data
}

FUNCTION setup_variables
{
    PROCESS {

        $original_file_data = read_user_data_file
        $fake_pass = "*" * ($original_file_data."okta_password").length
        $okta_password = Read-Host -Prompt "Input your okta password [$fake_pass]" -AsSecureString
        $BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($okta_password)
        $okta_password = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)
        if ($okta_password.length -eq 0)
        {
            $okta_password = $original_file_data."okta_password"
        }


        $default_work_directory = $original_file_data."work_directory"
        if ($default_work_directory.length -eq 0)
        {
            $default_work_directory = "$env:USERPROFILE\work"
        }
        if (!($work_directory = Read-Host -Prompt "Work Directory [Current: $default_work_directory | default: $env:USERPROFILE\work]"))
        {
            $work_directory = $default_work_directory
        }
        if ($work_directory -eq "default")
        {
            $work_directory = "$env:USERPROFILE\work"
        }

        $default_okta_username = $original_file_data."okta_username"
        if ($default_okta_username.length -eq 0)
        {
            $default_okta_username = "unknown_user"
        }
        if (!($okta_username = Read-Host -Prompt "Okta username [$default_okta_username]"))
        {
            $okta_username = $default_okta_username
        }
        if ($okta_username -eq "unknown_user")
        {
            Write-Error "Username is required. Try again."
        }
        $Date = Get-Date

        $filedata =
        "okta_password=$okta_password
okta_username=$okta_username
work_directory=$work_directory
date=$Date"

        $filedata | set-content "$env:USERPROFILE/.dp_script"
    }
}



Function generate_saml2aws_file
{

    process
    {
        $OFS = "`r`n"
        $original_file_data = read_user_data_file
        $roles_data = Get-Content "$SCRIPT_PARENT_PATH\..\roles\data_platform_roles.json" | ConvertFrom-Json

        $filedata = ""
        $roles_data | Get-Member -MemberType NoteProperty | ForEach-Object {
            $account = $_.Name
            $account_id = $roles_data."$account"."account_id"
            $okta_url = $roles_data."$account"."okta_url"
            $roles = $roles_data."$account"."roles"
            $okta_username = $original_file_data."okta_username"


            foreach ($role in $roles){
                $role_name = $role."name"
                $role_arn = "arn:aws:iam::" + $account_id + ":role/" + $role_name
                $filedata = $filedata + @"
$OFS
[$account-$role_name]
Name                 = $account-$role_name
app_id               =
url                  = $okta_url
username             = $okta_username
provider             = Okta
mfa                  = PUSH
skip_verify          = false
timeout              = 0
aws_urn              = urn:amazon:webservices
aws_session_duration = 3600
aws_profile          = $account-$role_name
resource_id          =
subdomain            =
role_arn             = $role_arn
region               =
http_attempts_count  =
http_retry_delay     =
credentials_file     =
saml_cache           = false
"@
            }
        }

        $filedata | set-content $env:USERPROFILE\.saml2aws
    }
}


if ($command -eq "complete")
{
    Write-Output "Compelte Setup"
    setup_variables
    setup_profile
    setup_dp_module
}
else
{

    Write-Output "Running Script: '$SCRIPT_PARENT_PATH\deploy.ps1'"

    setup_profile
    generate_saml2aws_file
    setup_dp_module

    Write-Warning "Please close this Powershell and restart."

}