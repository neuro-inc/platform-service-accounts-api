IMAGE_NAME ?= platformserviceaccountsapi
IMAGE_TAG ?= latest
ARTIFACTORY_TAG ?= $(shell echo $${GITHUB_REF\#refs/tags/v})
IMAGE ?= $(GKE_DOCKER_REGISTRY)/$(GKE_PROJECT_ID)/$(IMAGE_NAME)
IMAGE_AWS ?= $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com/$(IMAGE_NAME)

PLATFORMAUTHAPI_TAG=1deed1143a3cdf00a7522ad7d40d7794dcfe7ef1

export PIP_EXTRA_INDEX_URL ?= $(shell python pip_extra_index_url.py)

setup:
	@echo "Using extra pip index: $(PIP_EXTRA_INDEX_URL)"
	pip install -r requirements/test.txt
	pre-commit install

lint: format
	mypy platform_service_accounts_api tests setup.py

format:
ifdef CI_LINT_RUN
	pre-commit run --all-files --show-diff-on-failure
else
	pre-commit run --all-files
endif

test_unit:
	pytest -vv --cov=platform_service_accounts_api --cov-report xml:.coverage-unit.xml tests/unit

test_integration:
	pytest -vv --maxfail=3 --cov=platform_service_accounts_api --cov-report xml:.coverage-integration.xml tests/integration

build:
	python setup.py sdist
	docker build \
		--build-arg PIP_EXTRA_INDEX_URL \
		--build-arg DIST_FILENAME=`python setup.py --fullname`.tar.gz \
		-t $(IMAGE_NAME):$(IMAGE_TAG) .

docker_pull_test_images:
	@eval $$(minikube docker-env); \
	    docker pull $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com/platformauthapi:$(PLATFORMAUTHAPI_TAG); \
	    docker tag $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com/platformauthapi:$(PLATFORMAUTHAPI_TAG) platformauthapi:latest

eks_login:
	aws eks --region $(AWS_REGION) update-kubeconfig --name $(AWS_CLUSTER_NAME)

ecr_login:
	$$(aws ecr get-login --no-include-email --region $(AWS_REGION))

aws_docker_push: build ecr_login
	docker tag $(IMAGE_NAME):$(IMAGE_TAG) $(IMAGE_AWS):latest
	docker tag $(IMAGE_NAME):$(IMAGE_TAG) $(IMAGE_AWS):$(GITHUB_SHA)
	docker push $(IMAGE_AWS):latest
	docker push $(IMAGE_AWS):$(GITHUB_SHA)

_helm:
	curl https://raw.githubusercontent.com/kubernetes/helm/master/scripts/get | bash -s -- -v v2.11.0

aws_k8s_deploy: _helm
	helm -f deploy/platformserviceaccountsapi/values-$(HELM_ENV).yaml --set "IMAGE=$(IMAGE_AWS):$(GITHUB_SHA)" upgrade --install platformserviceaccountsapi deploy/platformserviceaccountsapi/ --namespace platform --wait --timeout 600

artifactory_docker_push: build
	docker tag $(IMAGE_NAME):$(IMAGE_TAG) $(ARTIFACTORY_DOCKER_REPO)/$(IMAGE_NAME):$(ARTIFACTORY_TAG)
	docker login $(ARTIFACTORY_DOCKER_REPO) --username=$(ARTIFACTORY_USERNAME) --password=$(ARTIFACTORY_PASSWORD)
	docker push $(ARTIFACTORY_DOCKER_REPO)/$(IMAGE_NAME):$(ARTIFACTORY_TAG)

artifactory_helm_push: _helm
	mkdir -p temp_deploy/platformserviceaccountsapi
	cp -Rf deploy/platformserviceaccountsapi/. temp_deploy/platformserviceaccountsapi
	cp temp_deploy/platformserviceaccountsapi/values-template.yaml temp_deploy/platformserviceaccountsapi/values.yaml
	sed -i "s/IMAGE_TAG/$(ARTIFACTORY_TAG)/g" temp_deploy/platformserviceaccountsapi/values.yaml
	find temp_deploy/platformserviceaccountsapi -type f -name 'values-*' -delete
	helm init --client-only
	helm package --app-version=$(ARTIFACTORY_TAG) --version=$(ARTIFACTORY_TAG) temp_deploy/platformserviceaccountsapi/
	helm plugin install https://github.com/belitre/helm-push-artifactory-plugin
	helm push-artifactory $(IMAGE_NAME)-$(ARTIFACTORY_TAG).tgz $(ARTIFACTORY_HELM_REPO) --username $(ARTIFACTORY_USERNAME) --password $(ARTIFACTORY_PASSWORD)
