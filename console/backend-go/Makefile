.PHONY: proto build run test lint clean

MODULE := github.com/fengzhizi319/privacy-local-agent/console/backend-go
PROTOC := /tmp/protoc-install/bin/protoc

# Generate Go gRPC stubs from the shared proto file.
# The proto file is located at the repository root under proto/privacy.proto.
proto:
	mkdir -p proto
	$(PROTOC) -I ../../proto \
		--go_out=proto \
		--go_opt=paths=source_relative \
		--go-grpc_out=proto \
		--go-grpc_opt=paths=source_relative \
		../../proto/privacy.proto

# Download dependencies and build the server binary.
build:
	go mod tidy
	go build -o bin/backend-go ./cmd/server

# Run the server in development mode.
run:
	go mod tidy
	go build -o bin/backend-go ./cmd/server
	./bin/backend-go

# Run the server in development mode (legacy alias).
dev: run

# Run all tests (unit + integration).
# Integration tests require privacy_local_agent to be running on 127.0.0.1:50051.
test:
	go test ./...

# Run only unit tests.
test-unit:
	go test -short ./...

# Run only integration tests.
test-integration:
	go test -run Integration ./tests/...

# Format and lint.
lint:
	go fmt ./...
	go vet ./...

# Clean build artifacts and generated code.
clean:
	rm -rf bin
	rm -f proto/*.pb.go
