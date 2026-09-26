package main

import "testing"

func TestMSYSPipeNames(t *testing.T) {
	for name, want := range map[string]bool{
		`\msys-1888ae32e00d56aa-pty0-from-master`:                  true,
		`\msys-1888ae32e00d56aa-pty3-to-master`:                    true,
		`\cygwin-e022582115c10879-pty1-to-master`:                  true,
		`\Device\NamedPipe\msys-1888ae32e00d56aa-pty0-from-master`: true,
		`\msys-1888ae32e00d56aa-cygwin-lpc`:                        false,
		`\Winsock2\CatalogChangeListener-3a8-0`:                    false,
		``:                                                         false,
	} {
		if got := isMSYSPipeName(name); got != want {
			t.Errorf("isMSYSPipeName(%q) = %v, want %v", name, got, want)
		}
	}
}
