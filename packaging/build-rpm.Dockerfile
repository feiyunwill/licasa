FROM fedora:44

RUN dnf install -y --setopt=install_weak_deps=False \
    rpm-build python3 && dnf clean all

WORKDIR /work
