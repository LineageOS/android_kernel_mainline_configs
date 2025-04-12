#!/bin/bash

for arch in arm64; do
	grep -v ' is not set' defconfigs/$arch/gki_defconfig > fragments/y/$arch/gki.config
done
