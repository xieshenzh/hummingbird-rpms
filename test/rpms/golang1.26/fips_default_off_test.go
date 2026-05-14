package main

import (
	"crypto/fips140"
	"debug/buildinfo"
	"fmt"
	"os"
	"strings"
)

func main() {
	exitCode := 0

	fmt.Println("=== Test: fips140.Enabled() matches expectation ===")
	expectFIPS := os.Getenv("EXPECT_FIPS") == "1"
	if fips140.Enabled() != expectFIPS {
		fmt.Printf("FAIL: fips140.Enabled() = %v, expected %v\n", fips140.Enabled(), expectFIPS)
		exitCode = 1
	} else {
		fmt.Printf("PASS: fips140.Enabled() = %v\n", fips140.Enabled())
	}

	fmt.Println("=== Test: build settings ===")
	self, err := os.Executable()
	if err != nil {
		fmt.Printf("FAIL: cannot find executable: %v\n", err)
		os.Exit(1)
	}
	info, err := buildinfo.ReadFile(self)
	if err != nil {
		fmt.Printf("FAIL: buildinfo.ReadFile failed: %v\n", err)
		os.Exit(1)
	}

	var gofips140 string
	var defaultGodebug string
	for _, s := range info.Settings {
		switch s.Key {
		case "GOFIPS140":
			gofips140 = s.Value
		case "DefaultGODEBUG":
			defaultGodebug = s.Value
		}
	}

	fmt.Println("=== Test: certified FIPS module embedded ===")
	if gofips140 == "" {
		fmt.Println("FAIL: GOFIPS140 not found in build settings")
		exitCode = 1
	} else if !strings.HasPrefix(gofips140, "v1.0.0") {
		fmt.Printf("FAIL: GOFIPS140 = %s, expected v1.0.0-*\n", gofips140)
		exitCode = 1
	} else {
		fmt.Printf("PASS: GOFIPS140 = %s\n", gofips140)
	}

	fmt.Println("=== Test: DefaultGODEBUG contains fips140=auto ===")
	if !strings.Contains(defaultGodebug, "fips140=auto") {
		fmt.Printf("FAIL: DefaultGODEBUG = %s (missing fips140=auto)\n", defaultGodebug)
		exitCode = 1
	} else {
		fmt.Println("PASS: DefaultGODEBUG contains fips140=auto")
	}

	if exitCode == 0 {
		fmt.Println("\nAll tests passed")
	} else {
		fmt.Println("\nSome tests failed")
	}
	os.Exit(exitCode)
}
