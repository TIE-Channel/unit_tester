// CI for the ut engine itself. Repositories that use ut start from
// ci/Jenkinsfile.consumer instead.

pipeline {
    agent { label 'linux' }  // TODO: any agent with python3 >= 3.9

    options {
        timeout(time: 30, unit: 'MINUTES')
    }

    stages {
        stage('Install') {
            steps {
                sh '''
                    python3 -m venv .venv
                    .venv/bin/pip install --quiet --upgrade pip
                    .venv/bin/pip install --quiet -e ".[parallel]"
                '''
            }
        }
        stage('Engine tests') {
            steps {
                sh '.venv/bin/python -m pytest tests -n auto --junitxml=reports/engine.xml'
            }
        }
        stage('Demo repository') {
            steps {
                dir('examples/demo_repo') {
                    sh '../../.venv/bin/ut validate'
                    sh '../../.venv/bin/ut run --junit ../../reports/demo.xml'
                }
            }
        }
    }

    post {
        always {
            junit testResults: 'reports/*.xml', allowEmptyResults: true
        }
    }
}
