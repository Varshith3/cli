Function install_commands
{
    param (
        [Parameter(
                Mandatory = $True,
                Valuefrompipeline = $true)]
        [String[]] $args
    )

    PROCESS {
        if (-Not(@($args).length -eq 1))
        {
            Write-Error "Invalid install Command. Please use like: dp install python"
        }
        $tool = $args[0]

        if ($tool -eq "pyenv")
        {
            if (-Not(Test-Path -Path "$HOME/.pyenv"))
            {
                git clone https://github.com/pyenv-win/pyenv-win.git "$HOME/.pyenv"
            }
            else
            {
                git -C "$HOME/.pyenv" pull
            }
        }
        elseif ($tool -eq "python")
        {
            pyenv install 3.8.7
            pyenv global 3.8.7
            pyenv rehash
            pip install pipenv
            pyenv rehash
            pyenv --version
        }
        else
        {
            Write-Error "Command: $command not found."
        }
    }

}

Function load_core_variables
{

    PROCESS {
        $dp_script_file = "$env:USERPROFILE/.dp_script"
        $original_file_data = ""
        if (Test-Path $dp_script_file -PathType Leaf)
        {
            $original_file_data = get-content $dp_script_file -raw
        }
        $original_file_data = $original_file_data -replace '\\', '\\'
        $original_file_data = convertfrom-stringdata($original_file_data)
        $env:WORK_DIR = $original_file_data."work_directory"

        $env:WORK_DIR = "$env:USERPROFILE\work"
        $env:INSTALLATION_DIR = "$env:WORK_DIR\installations"
        $env:PLATFORM_ENGINEERING = "$env:WORK_DIR\platform_engineering"
        $env:DATA_PRODUCTS = "$env:WORK_DIR\data_products"
        $env:PLATFORM_LOCAL_SETUP = "$env:PLATFORM_ENGINEERING\code\gh-dp-platform-local-setup"
        $DOCUMENT_PATH = [Environment]::GetFolderPath("MyDocuments")
        $WINDOWS_POWERSHELL_PATH = "$DOCUMENT_PATH\WindowsPowerShell"
        $env:WINDOWS_MODULE_PATH = "$WINDOWS_POWERSHELL_PATH\Modules"
    }

    END {
        Write-Output "Core variables have been loaded."
    }
}

Function load_password
{
    PROCESS {
        $dp_script_file = "$env:USERPROFILE/.dp_script"
        $original_file_data = ""
        if (Test-Path $dp_script_file -PathType Leaf)
        {
            $original_file_data = get-content $dp_script_file -raw
        }
        $original_file_data = $original_file_data -replace '\\', '\\'
        $original_file_data = convertfrom-stringdata($original_file_data)
        $original_file_data."okta_password"
    }
}

Function load_path_variables
{

    PROCESS {
        #       Download poppler - https://github.com/oschwartz10612/poppler-windows/releases/
        #       This is required for converting pdf to images.
        $env:POPPLER_HOME = "$env:INSTALLATION_DIR\poppler"
        $env:JAVA_HOME = "$env:INSTALLATION_DIR\jdk1.8.0_301"
        $env:SCALA_HOME = "$env:INSTALLATION_DIR\scala-2.12.13"
        $env:FLINK_HOME = "$env:INSTALLATION_DIR\flink-1.12.1"
        $env:KAFKA_HOME = "$env:INSTALLATION_DIR\kafka_2.12-2.7.0"
        $env:MAVEN_HOME = "$env:INSTALLATION_DIR\apache-maven-3.6.3"
        $env:PYENV = "$env:USERPROFILE\.pyenv\pyenv-win"
        $env:PYENV_HOME = "$env:USERPROFILE\.pyenv\pyenv-win"
        $env:TERRAFORM_HOME = "$env:INSTALLATION_DIR\terraform"
        $env:AWS_DEFAULT_PROFILE = "saml"
        $env:Path = "$env:FLINK_HOME\bin;$env:KAFKA_HOME\bin;" + $env:Path
        $env:Path = "$env:MAVEN_HOME\bin;$env:SCALA_HOME\bin;" + $env:Path
        $env:Path = "$env:TERRAFORM_HOME;$env:JAVA_HOME\bin;" + $env:Path

        $USER_PATH = [Environment]::GetEnvironmentVariable('PATH', 'User')
        if (-Not("$USER_PATH" -Match "pyenv"))
        {
            [Environment]::SetEnvironmentVariable("PYENV", "$env:PYENV", "User")
            [Environment]::SetEnvironmentVariable("PYENV_HOME", "$env:PYENV", "User")
            [Environment]::SetEnvironmentVariable("PATH", "$env:PYENV\bin\;$env:PYENV\shims\;$USER_PATH", "User")
        }

        if (-Not("$USER_PATH" -Match "poppler"))
        {
            [Environment]::SetEnvironmentVariable("PATH", "$env:POPPLER_HOME\Library\bin;$USER_PATH", "User")
        }
    }

    END {
        Write-Output "All path variables have been updated."
    }
}

Function aws_cmds
{
    param (
        [Parameter(
                Mandatory = $True,
                Valuefrompipeline = $true)]
        [String[]] $args
    )

    PROCESS {
        if (-Not(@($args).length -eq 2))
        {
            Write-Error "Invalid AWS Command. Please use like: dp aws p admin| dp aws np dataops"

        }
        Write-Output $env:WINDOWS_MODULE_PATH
        $account_input = $args[0]
        $short_role_input = $args[1]

        $pass = load_password
        Write-Output "$pass"

        $roles_data = Get-Content "$env:WINDOWS_MODULE_PATH\dp\data_platform_roles.json" | ConvertFrom-Json

        $roles_data | Get-Member -MemberType NoteProperty | ForEach-Object {
            $account = $_.Name
            $roles = $roles_data."$account"."roles"
            $found = "false"
            foreach ($role in $roles){
                $short_name = $role."short_name"
                $role_name = $role."name"
                if (("$account" -eq "$account_input") -and ("$short_name" -eq "$short_role_input"))
                {
                    Write-Output "Using: $account-$role_name"
                    $env:AWS_PROFILE = "$account-$role_name"
                    $env:SAML2AWS_IDP_ACCOUNT = "$account-$role_name"
                    $found = "true"
                    saml2aws login --force --username="jvirdee" --password="$pass"
                    break;
                }
            }
            if ( "$found" -eq "true" ) {
                break;
            }
        }

    }
}



Function cloneRep($gitPath, $path)
{

    if (-Not(Test-Path -Path $path))
    {
        New-Item -path $path -type directory -ErrorAction SilentlyContinue
        git clone $gitPath $path
        Write-Output "Cloned path for Git: $gitPath, Path: $path"
    }
    else
    {
        git --git-dir $path/.git remote set-url origin $gitPath
        Write-Output "Changed path for Git: $gitPath, Path: $path"
    }

}



Function clone_repositories
{
    PROCESS {

        #        These are the repositories from the GuardanthHealth Data Platform's repositories

        $PLATFORM_ENGINEERING_CODE = "$env:PLATFORM_ENGINEERING\code"

        $DATA_PRODUCTS_CODE = "$env:DATA_PRODUCTS\code"

        cloneRep git@github.com:guardant/terraform-aws-gh-dp-ecs-fargate.git  "$PLATFORM_ENGINEERING_CODE\terraform-aws-gh-dp-ecs-fargate"
        cloneRep git@github.com:guardant/terraform-aws-gh-dp-amundsen.git "$PLATFORM_ENGINEERING_CODE\terraform-aws-gh-dp-amundsen"

        cloneRep git@github.com:guardant/terraform-aws-gh-dp-fargate-jenkins.git  "$PLATFORM_ENGINEERING_CODE\terraform-aws-gh-dp-fargate-jenkins"
        cloneRep git@github.com:guardant/terraform-aws-gh-dp-network.git "$PLATFORM_ENGINEERING_CODE\terraform-aws-gh-dp-network"
        cloneRep git@github.com:guardant/terraform-aws-gh-dp-security.git "$PLATFORM_ENGINEERING_CODE\terraform-aws-gh-dp-security"


        cloneRep git@github.com:guardant/terraform-aws-gh-dp.git "$PLATFORM_ENGINEERING_CODE\terraform-aws-gh-dp"
        cloneRep git@github.com:guardant/terraform-aws-gh-dp-alb.git "$PLATFORM_ENGINEERING_CODE\terraform-aws-gh-dp-alb"
        cloneRep git@github.com:guardant/terraform-aws-gh-dp-es.git "$PLATFORM_ENGINEERING_CODE\terraform-aws-gh-dp-es"

        cloneRep git@github.com:guardant/terraform-aws-gh-dp-module-template.git "$PLATFORM_ENGINEERING_CODE\terraform-aws-gh-dp-module-template"

        cloneRep git@github.com:guardant/terraform-aws-gh-dp-sg.git "$PLATFORM_ENGINEERING_CODE\terraform-aws-gh-dp-sg"

        cloneRep git@github.com:guardant/terraform-aws-gh-dp-dms-database-replication.git "$PLATFORM_ENGINEERING_CODE\terraform-aws-gh-dp-dms-database-replication"
        cloneRep git@github.com:guardant/terraform-aws-gh-dp-airflow.git "$PLATFORM_ENGINEERING_CODE\terraform-aws-gh-dp-airflow"
        cloneRep git@github.com:guardant/terraform-aws-gh-dp-sagemaker-notebook.git "$PLATFORM_ENGINEERING_CODE\terraform-aws-gh-dp-sagemaker-notebook"
        cloneRep git@github.com:guardant/terraform-aws-gh-dp-kinesis-analytics-app.git "$PLATFORM_ENGINEERING_CODE\terraform-aws-gh-dp-kinesis-analytics-app"
        cloneRep git@github.com:gh-org-data-platform/terraform-aws-gh-dp-redshift.git "$PLATFORM_ENGINEERING_CODE\terraform-aws-gh-dp-redshift"


        cloneRep git@github.com:gh-vadepu/amundsen.git "$PLATFORM_ENGINEERING_CODE\amundsen"
        cloneRep git@github.com:guardant/gh-aws.git "$PLATFORM_ENGINEERING_CODE\gh-aws"



        cloneRep git@github.com:guardant/gh-dp-data-product-lims-raw.git "$DATA_PRODUCTS_CODE\gh-dp-data-product-lims-raw"



    }
}


load_core_variables

Function dp
{

    [CmdletBinding()]
    param (

        [Parameter(Mandatory = $true,
                HelpMessage = "Command to execute")]
        [string]$command,

        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]] $args
    )

    PROCESS {
        if ($command -eq "update")
        {
            git -C $env:PLATFORM_LOCAL_SETUP pull
        }

        elseif ( ($command -eq "setup") -and (@($args).length -eq 0))
        {
            & "$env:PLATFORM_LOCAL_SETUP\windows\setup.ps1"
        }

        elseif ( ($command -eq "setup") -and ($args[0] -eq "complete"))
        {
            & "$env:PLATFORM_LOCAL_SETUP\windows\setup.ps1" complete
        }
        elseif ($command -eq "clone")
        {
            clone_repositories
        }

        elseif ($command -eq "load")
        {
            load_path_variables
        }


        elseif ($command -eq "aws")
        {
            aws_cmds $args
        }
        elseif ($command -eq "install")
        {
            install_commands $args
        }
        else
        {
            Write-Error "Command: $command not found."
        }

    }


}

Export-ModuleMember -Function dp
