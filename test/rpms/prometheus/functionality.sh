#!/bin/bash

# Shared runtime test for versioned Prometheus RPMs. Package name and version
# come from TEST_RPM so this remains valid when either LTS line is updated.

set -euo pipefail

rpm_name=$(rpm -qp --queryformat '%{NAME}' "${TEST_RPM}")
rpm_version=$(rpm -qp --queryformat '%{VERSION}' "${TEST_RPM}")

if [[ "${rpm_name}" == *-debuginfo || "${rpm_name}" == *-debugsource ]]; then
    echo "SKIP: ${rpm_name} is a debug package; Prometheus functionality check is not applicable"
    exit 0
fi

if [[ "${rpm_name}" != "${PACKAGE_NAME}" ]]; then
    echo "FAIL: test package ${PACKAGE_NAME} does not match RPM ${rpm_name}"
    exit 1
fi

build_dir=$(mktemp -d)
cleanup() {
    rm -rf "${build_dir}"
}
trap cleanup EXIT

mkdir -p "${build_dir}/local-repo"
if [[ -n ${TEST_REPO_DIR:-} ]]; then
    cp "${TEST_REPO_DIR}"/*.rpm "${build_dir}/local-repo/"
else
    cp "${TEST_RPM}" "${build_dir}/local-repo/"
fi

createrepo_c "${build_dir}/local-repo" || \
    "${TEST_ENGINE}" run --rm --user 0 \
        -v "${build_dir}/local-repo:/repo:z" \
        quay.io/hummingbird-ci/hummingbird-builder:latest \
        createrepo_c /repo

"${TEST_ENGINE}" run --rm \
    -v "${build_dir}/local-repo:/tmp/local-repo:ro,z" \
    -e "RPM_NAME=${rpm_name}" \
    -e "RPM_VERSION=${rpm_version}" \
    quay.io/hummingbird-ci/hummingbird-builder:latest \
    bash -c '
        set -euo pipefail

        printf "[local-repo]\\nname=Local\\nbaseurl=file:///tmp/local-repo\\nenabled=1\\ngpgcheck=0\\npriority=1\\n" \
            > /etc/yum.repos.d/local.repo
        dnf install -y "${RPM_NAME}" curl

        fail() {
            echo "FAIL: $1"
            exit 1
        }

        [[ -x /usr/bin/prometheus ]] || fail "/usr/bin/prometheus is not executable"
        [[ -x /usr/bin/promtool ]] || fail "/usr/bin/promtool is not executable"

        prometheus_version=$(prometheus --version 2>&1 | head -n1)
        [[ "${prometheus_version}" == *"version ${RPM_VERSION}"* ]] || \
            fail "prometheus reports ${prometheus_version}, expected ${RPM_VERSION}"

        promtool_version=$(promtool --version 2>&1 | head -n1)
        [[ "${promtool_version}" == *"version ${RPM_VERSION}"* ]] || \
            fail "promtool reports ${promtool_version}, expected ${RPM_VERSION}"

        config=$(mktemp)
        data_dir=$(mktemp -d)
        log=$(mktemp)
        cleanup() {
            if [[ -n ${prometheus_pid:-} ]]; then
                kill "${prometheus_pid}" 2>/dev/null || true
                wait "${prometheus_pid}" 2>/dev/null || true
            fi
            rm -rf "${config}" "${data_dir}" "${log}"
        }
        trap cleanup EXIT

        cat > "${config}" <<EOF
global:
  scrape_interval: 1s
scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["127.0.0.1:19090"]
EOF

        promtool check config "${config}"

        prometheus --config.file="${config}" \
            --storage.tsdb.path="${data_dir}" \
            --web.listen-address=127.0.0.1:19090 \
            >"${log}" 2>&1 &
        prometheus_pid=$!

        ready=false
        for _ in $(seq 1 30); do
            if curl --fail --silent http://127.0.0.1:19090/-/ready >/dev/null; then
                ready=true
                break
            fi
            sleep 1
        done
        ${ready} || {
            cat "${log}"
            fail "Prometheus did not become ready"
        }

        pprof_status=$(curl --silent --output /dev/null --write-out "%{http_code}" \
            http://127.0.0.1:19090/debug/pprof/)
        [[ "${pprof_status}" == "404" ]] || \
            fail "debug profiling endpoint returned HTTP ${pprof_status}, expected 404"

        metrics=$(curl --fail --silent http://127.0.0.1:19090/metrics)
        build_info=$(grep "^prometheus_build_info{" <<< "${metrics}" || true)
        [[ "${build_info}" == *"${RPM_VERSION}"* ]] || \
            fail "prometheus_build_info does not contain RPM version ${RPM_VERSION}"

        query_response=
        for _ in $(seq 1 30); do
            query_response=$(curl --fail --silent --get \
                --data-urlencode "query=prometheus_build_info" \
                http://127.0.0.1:19090/api/v1/query)
            if [[ "${query_response}" == *status*success* && \
                  "${query_response}" == *"${RPM_VERSION}"* ]]; then
                break
            fi
            sleep 1
        done
        [[ "${query_response}" == *status*success* && \
           "${query_response}" == *"${RPM_VERSION}"* ]] || \
            fail "instant query did not return prometheus_build_info: ${query_response}"

        homepage=$(curl --fail --location --silent http://127.0.0.1:19090/query)
        [[ "${homepage}" == *"assets/"* || "${homepage}" == *"static/"* ]] || \
            fail "Prometheus UI asset references missing from homepage"
        [[ "${homepage}" == *"Prometheus"* ]] || \
            fail "Prometheus UI content missing from homepage"

        echo "PASS: ${RPM_NAME} ${RPM_VERSION} installs and serves API and UI"
    '
