.PHONY: demo test lint security docker-demo integration
PYTHON ?= python3

demo:
	$(PYTHON) -m ghostdock demo

test:
	$(PYTHON) -m unittest discover -s tests -v

lint:
	flake8 ghostdock tests scripts demo main.py nodes.py --select E4,E7,E9,F
	black --check ghostdock tests scripts demo main.py nodes.py
	isort --check-only ghostdock tests scripts demo main.py nodes.py
	shellcheck runner.sh aws_runner.sh ghostdock/bootstrap.sh scripts/*.sh
	cfn-lint infra/lab-foundation.yaml

security:
	bandit -c pyproject.toml -r ghostdock demo

docker-demo:
	docker build -t ghostdock-demo:local demo

integration:
	$(PYTHON) scripts/docker_integration.py
