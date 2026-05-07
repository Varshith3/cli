@Library("shared-library") _

pipeline {
    agent {
        label 'builds'
    }

    tools {
        maven 'Maven3'
    }

    parameters{
        choice(
                name: 'TARGET_WORKSPACE',
                choices: getEnvironmentList(account:account_name),
                description: 'Infra deployment workspace'
        )
        booleanParam(name: 'MAVEN_BUILD', defaultValue: false, description: 'The code base will be maven compile, run tests and package')
        booleanParam(name: 'APPLY', defaultValue: false, description: 'The infra will be applied to the chosen workspace.')
    }

    environment {
        REGION = 'us-west-2'
        ACCOUNT_NAME = credentials('account_name')
    }

    stages {
        stage("Initialize Workspace"){
            steps{
                dir('terraform'){
                    downloadDependencies(
                        directory: ".dependencies",
                        repository:"gh-org-data-platform/terraform-aws-gh-dp-infra-templates",
                        account:"${env.ACCOUNT_NAME}",
                        branch: "v1.93.0"
                    )                    
                    initWorkspace(
                            repo:"${env.GIT_URL}",
                            account:"${env.ACCOUNT_NAME}",
                            env: "${params.TARGET_WORKSPACE}"
                    )
                }
            }
        }
        stage("Validate & Plan") {
            steps {
                dir('terraform') {
                    validateAndPlan(
                            account:"${env.ACCOUNT_NAME}",
                            env: "${params.TARGET_WORKSPACE}"
                    )
                }
            }
        }
        stage("Maven Test & Build") {
            when {expression { params.MAVEN_BUILD == true }}
            steps {
                dir('code') {
                    dir('scala') {
                        mavenBuild(
                                build: "${params.MAVEN_BUILD}",
                                branch: env.BRANCH_NAME
                        )
                    }
                }
            }
        }
        stage("Apply") {
            when {expression { params.APPLY == true }}
            steps {
                dir('terraform') {
                    terraformApply(
                            account:"${env.ACCOUNT_NAME}",
                            apply: "${params.APPLY}",
                            branch: env.BRANCH_NAME,
                            env: "${params.TARGET_WORKSPACE}"
                    )
                }
            }
        }
    }
post {
        always {
            pushLogsAndMetadata()        
        }
    }   
}
