FROM archlinux:latest

RUN pacman -Syu --noconfirm --needed base-devel \
    && pacman -Scc --noconfirm \
    && useradd --create-home builder

WORKDIR /work
