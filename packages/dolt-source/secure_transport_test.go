package mysql

import (
	"context"
	"io"
	"net"
	"testing"
	"time"
)

// A protocol error alone is insufficient: rejecting insecure transport must
// close the server connection before any later authentication response.
func TestSecureTransportRefusalIsTerminal(t *testing.T) {
	for _, tc := range []struct {
		name     string
		required bool
		password string
	}{
		{name: "optional-valid", password: "test-password"},
		{name: "required-valid", required: true, password: "test-password"},
		{name: "required-invalid", required: true, password: "wrong-password"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			auth := NewAuthServerStatic("", "", 0)
			auth.entries["transport-test"] = []*AuthServerStaticEntry{{Password: "test-password"}}
			defer auth.close()
			listener := &Listener{
				authServer:             auth,
				handler:                &testHandler{},
				ServerVersion:          DefaultServerVersion,
				connReadBufferSize:     DefaultConnBufferSize,
				shutdownCh:             make(chan struct{}),
				RequireSecureTransport: tc.required,
			}
			serverSide, clientSide := net.Pipe()
			deadline := time.Now().Add(5 * time.Second)
			if err := serverSide.SetDeadline(deadline); err != nil {
				t.Fatal(err)
			}
			if err := clientSide.SetDeadline(deadline); err != nil {
				t.Fatal(err)
			}
			done := make(chan struct{})
			connCount.Add(1)
			go func() {
				defer close(done)
				listener.handle(context.Background(), serverSide, 1, time.Now())
			}()
			t.Cleanup(func() {
				clientSide.Close()
				serverSide.Close()
				select {
				case <-done:
				case <-time.After(5 * time.Second):
					t.Error("server handler did not stop after closing its owned pipe")
				}
			})

			// Unlike Connect, this internal handshake leaves the client endpoint
			// open after an error so only the server can establish terminal EOF.
			client := newConn(clientSide)
			err := client.clientHandshake(&ConnParams{Uname: "transport-test", Pass: tc.password})
			if !tc.required {
				if err != nil {
					t.Fatalf("optional transport handshake failed: %v", err)
				}
				if err := client.Ping(); err != nil {
					t.Fatalf("positive protocol control failed: %v", err)
				}
				client.Close()
			} else {
				const refusal = "unknown error: Code: UNAVAILABLE\nserver does not allow insecure connections, client must use SSL/TLS\n"
				sqlError, ok := err.(*SQLError)
				if !ok || sqlError.Num != ERUnknownError || sqlError.State != SSUnknownSQLState || sqlError.Message != refusal {
					t.Fatalf("expected exact secure transport refusal, got %v", err)
				}
				// Read through the existing buffer, not the raw pipe: any bytes
				// prefetched after the refusal must also cause a failure.
				var next [1]byte
				n, readErr := client.bufferedReader.Read(next[:])
				if n != 0 || readErr != io.EOF {
					t.Fatalf("refusal was not terminal: bytes=%d error=%v", n, readErr)
				}
			}
			select {
			case <-done:
			case <-time.After(5 * time.Second):
				t.Fatal("server handler did not finish normally")
			}
		})
	}
}
