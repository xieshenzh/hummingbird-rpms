#!/bin/bash
set -ex

echo "Testing Swift version..."
swift --version

echo "Testing script execution..."
cat << 'EOF' > hello.swift
print("Hello, Scripting World!")
EOF
swift hello.swift | grep "Hello, Scripting World!"

echo "Testing compiled executable..."
cat << 'EOF' > main.swift
print("Hello, Compiled World!")
EOF
swiftc main.swift -o hello_compiled
./hello_compiled | grep "Hello, Compiled World!"

echo "Testing Swift Package Manager..."
mkdir mypkg
cd mypkg
swift package init --type executable
swift build
swift run | grep "Hello, world!"
cd ..

echo "Cleaning up..."
rm -f hello.swift main.swift hello_compiled
rm -rf mypkg

echo "All basic tests passed!"
