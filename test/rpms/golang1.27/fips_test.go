package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/fips140"
	"crypto/rand"
	"crypto/sha256"
	"debug/buildinfo"
	"fmt"
	"os"
)

func main() {
	exitCode := 0

	// Test 1: fips140.Enabled() must be true for GOFIPS140-built binaries
	fmt.Println("=== Test: fips140.Enabled() ===")
	if !fips140.Enabled() {
		fmt.Println("FAIL: fips140.Enabled() returned false")
		exitCode = 1
	} else {
		fmt.Println("PASS: fips140.Enabled() == true")
	}

	// Test 2: SHA-256 (FIPS-approved algorithm)
	fmt.Println("=== Test: SHA-256 ===")
	h := sha256.New()
	h.Write([]byte("fips140 test"))
	digest := fmt.Sprintf("%x", h.Sum(nil))
	if len(digest) != 64 {
		fmt.Println("FAIL: unexpected SHA-256 digest length")
		exitCode = 1
	} else {
		fmt.Println("PASS: SHA-256 succeeded")
	}

	// Test 3: ECDSA P-256 signing (FIPS-approved algorithm)
	fmt.Println("=== Test: ECDSA P-256 signing ===")
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		fmt.Printf("FAIL: ECDSA P-256 key generation failed: %v\n", err)
		exitCode = 1
	} else {
		d := sha256.Sum256([]byte("fips140 ecdsa test"))
		_, serr := ecdsa.SignASN1(rand.Reader, key, d[:])
		if serr != nil {
			fmt.Printf("FAIL: ECDSA P-256 signing failed: %v\n", serr)
			exitCode = 1
		} else {
			fmt.Println("PASS: ECDSA P-256 signing succeeded")
		}
	}

	// Test 4: Binary contains CMVP #5247 validated module build tag
	fmt.Println("=== Test: GOFIPS140=v1.0.0-c2097c7c build tag ===")
	self, serr := os.Executable()
	if serr != nil {
		fmt.Printf("FAIL: cannot find executable: %v\n", serr)
		exitCode = 1
	} else {
		info, berr := buildinfo.ReadFile(self)
		if berr != nil {
			fmt.Printf("FAIL: buildinfo.ReadFile failed: %v\n", berr)
			exitCode = 1
		} else {
			found := false
			for _, s := range info.Settings {
				if s.Key == "GOFIPS140" && s.Value == "v1.0.0-c2097c7c" {
					found = true
					break
				}
			}
			if found {
				fmt.Println("PASS: binary built with CMVP #5247 validated module")
			} else {
				fmt.Println("FAIL: GOFIPS140=v1.0.0-c2097c7c not found in build settings")
				exitCode = 1
			}
		}
	}

	if exitCode == 0 {
		fmt.Println("\nAll FIPS tests passed")
	} else {
		fmt.Println("\nSome FIPS tests failed")
	}
	os.Exit(exitCode)
}
