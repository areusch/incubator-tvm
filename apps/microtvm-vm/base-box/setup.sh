#!/bin/bash -e
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

apt update
apt install -y build-essential
useradd \
    -G adm,cdrom,dip,plugdev,lxd,lpadmin,sambashare,dialout,sudo \
    -p $(perl -e 'print crypt("tvm", "password")') \
    -m \
    -s /bin/bash \
    tvm

# Zephyr
wget --no-verbose https://apt.kitware.com/keys/kitware-archive-latest.asc
apt-key add kitware-archive-latest.asc
apt-add-repository 'deb https://apt.kitware.com/ubuntu/ focal main'
apt update
apt install -y --no-install-recommends git cmake ninja-build gperf \
  ccache dfu-util device-tree-compiler wget \
  python3-dev python3-pip python3-setuptools python3-tk python3-wheel xz-utils file \
  make gcc gcc-multilib g++-multilib libsdl2-dev \
  cmake

# Avahi, so that ssh tvm@microtvm works.
apt install -y avahi-daemon

OLD_HOSTNAME=$(hostname)
hostnamectl set-hostname microtvm
sed -i.bak "s/${OLD_HOSTNAME}/microtvm.localdomain/g" /etc/hosts

# Poetry deps
apt install -y python3-venv

# TVM deps
apt install -y llvm

# ONNX deps
apt install -y protobuf-compiler libprotoc-dev

chown tvm:tvm /home/tvm

# nrfjprog
cd ~vagrant
mkdir nrfjprog
wget --no-verbose -O nRFCommandLineTools1090Linuxamd64.tar.gz https://www.nordicsemi.com/-/media/Software-and-other-downloads/Desktop-software/nRF-command-line-tools/sw/Versions-10-x-x/10-9-0/nRFCommandLineTools1090Linuxamd64tar.gz
cd nrfjprog
tar -xzvf ../nRFCommandLineTools1090Linuxamd64.tar.gz
apt install -y ./JLink_Linux_V680a_x86_64.deb
apt install -y ./nRF-Command-Line-Tools_10_9_0_Linux-amd64.deb
source ~/.profile
nrfjprog --help

cp ~vagrant/setup-tvm-user.sh /home/tvm/setup-tvm-user.sh
chown tvm:tvm /home/tvm/setup-tvm-user.sh
chmod u+x /home/tvm/setup-tvm-user.sh
sudo -u tvm -sH ~tvm/setup-tvm-user.sh

sudo find ~tvm/zephyr-sdk -name '*.rules' -exec cp {} /etc/udev/rules.d \;
sudo udevadm control --reload

# Clean box for packaging as a base box
sudo apt-get clean
EMPTY_FILE="$HOME/EMPTY"
dd if=/dev/zero "of=${EMPTY_FILE}" bs=1M || /bin/true
if [ ! -e "${EMPTY_FILE}" ]; then
    echo "failed to zero empty sectors on disk"
    exit 2
fi
rm -f "${EMPTY_FILE}"
