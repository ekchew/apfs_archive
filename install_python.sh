#!/bin/bash

target=/usr/local/bin/python3
candidates=(
	/usr/bin/python3
	/opt/local/bin/python3
	/opt/homebrew/bin/python3
)

echo "This script installs a python3 executable where the APFS Archive"
echo "Automator app needs to see it at: $target"
echo ""

((n_avail=0))
for cand in "${candidates[@]}"; do
	[[ -x $cand ]] && avail[((n_avail++))]=$cand
done
if def_path=$(which python3); then
	[[ $def_path == $target ]] && found=true || found=false
	for path in "${avail[@]}"; do
		[[ $def_path == $path ]] && found=true
	done
	((!found)) && avail[((n_avail++))]=$def_path
fi

if [[ -x $target ]]; then
	echo "$target already exists"
	if [[ -L $target ]] && ((n_avail > 0)); then
		echo "it is a symlink"
		read -r -p "link another executable to that location? [yN] " res
		[[ $res == "y" ]] || exit 0
	else
		exit 0
	fi
elif (( n_avail == 0 )); then
	echo "no python3 executable could be found"
	read -r -p "install one through Xcode command line tools? [Yn] " res
	[[ $res == "n" ]] && exit 0
	xcode-select --install || exit 1
	echo "run this script again"
	exit 0
fi

echo "select an executable to install as $target:"
(( i=0 ))
for path in "${avail[@]}"; do
	((++i))
	printf "\t$i. $path\n"
done
read -r -p "enter number: " res
path="${avail[$((res - 1))]}"
[[ -z $path ]] && exit 1

echo "you may be asked for your admin password next"
if [[ -x $target ]]; then
	sudo rm -f $target || exit 1
else
	sudo mkdir -p $(dirname $target) || exit 1
fi
sudo ln -s "${path}" $target || exit 1
echo "installed"