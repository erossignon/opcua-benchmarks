// SPDX-License-Identifier: AGPL-3.0-or-later
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program. If not, see <https://www.gnu.org/licenses/>.
//
//    Copyright 2026 (c) o6 Automation GmbH (Author: Daniel Opitz)

package main

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"runtime"
	"runtime/debug"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"
	"unsafe"

	"github.com/gopcua/opcua/id"
	"github.com/gopcua/opcua/server"
	"github.com/gopcua/opcua/server/attrs"
	"github.com/gopcua/opcua/ua"
	"github.com/gopcua/opcua/uacp"
)

// The coordinator sends absolute Linux CLOCK_MONOTONIC deadlines; time.Now's
// wall-clock timestamp and Go's process-relative monotonic clock cannot be used.
func counterNow() int64 {
	var ts syscall.Timespec
	_, _, err := syscall.Syscall(syscall.SYS_CLOCK_GETTIME, 1, uintptr(unsafe.Pointer(&ts)), 0)
	if err != 0 {
		panic(err)
	}
	return ts.Nano()
}

type counterWindow struct {
	Event         string `json:"event"`
	Start         int64  `json:"start_ns"`
	End           int64  `json:"end_ns"`
	MeasuredStart int64  `json:"measured_start_ns"`
	MeasuredEnd   int64  `json:"measured_end_ns"`
	Interval      int64  `json:"interval_ns"`
}

func parseCounterWindow(line string) (counterWindow, error) {
	w := counterWindow{Event: "armed"}
	parts := strings.Fields(line)
	if len(parts) != 6 || parts[0] != "arm" {
		return w, fmt.Errorf("invalid control")
	}
	for i, target := range []*int64{&w.Start, &w.End, &w.MeasuredStart, &w.MeasuredEnd, &w.Interval} {
		value, err := strconv.ParseInt(parts[i+1], 10, 64)
		if err != nil {
			return w, err
		}
		*target = value
	}
	if w.Start <= counterNow() || w.MeasuredStart < w.Start || w.MeasuredEnd <= w.MeasuredStart ||
		w.End < w.MeasuredEnd || w.Interval <= 0 || (w.MeasuredEnd-w.MeasuredStart)%w.Interval != 0 ||
		(w.MeasuredEnd-w.MeasuredStart)/w.Interval > 1000 {
		return w, fmt.Errorf("invalid measurement window")
	}
	return w, nil
}

func produceCounters(ctx context.Context, srv *server.Server, nodes []*ua.NodeID, values []atomic.Uint64,
	w counterWindow, period int64, measureServer bool) map[string]any {
	var counts, intervals []uint64
	if measureServer {
		counts = make([]uint64, len(nodes))
		intervals = make([]uint64, (w.MeasuredEnd-w.MeasuredStart)/w.Interval)
	}
	due := w.Start
	for counterNow() < w.End {
		if delay := due - counterNow(); delay > 0 {
			timer := time.NewTimer(time.Duration(delay))
			select {
			case <-ctx.Done():
				timer.Stop()
				return nil
			case <-timer.C:
			}
		}
		for i, node := range nodes {
			now := counterNow()
			if now >= w.End || ctx.Err() != nil {
				break
			}
			// gopcua does not periodically sample ValueFunc. Update the value and
			// notify its subscription service through the SDK's public API.
			values[i].Add(1)
			srv.ChangeNotification(node)
			if measureServer && w.MeasuredStart <= now && now < w.MeasuredEnd {
				counts[i]++
				intervals[(now-w.MeasuredStart)/w.Interval]++
			}
		}
		// Skip missed slots instead of generating a catch-up burst.
		due = w.Start + ((counterNow()-w.Start)/period+1)*period
	}
	if measureServer {
		return map[string]any{"event": "result", "counts": counts, "interval_counts": intervals}
	}
	return map[string]any{"event": "done"}
}

func runCounter(args []string) error {
	if len(args) != 4 {
		return fmt.Errorf("expected port items sampling_ms measurement")
	}
	numbers := make([]int, 3)
	for i := range numbers {
		value, err := strconv.Atoi(args[i])
		if err != nil {
			return err
		}
		numbers[i] = value
	}
	port, items, sampling := numbers[0], numbers[1], numbers[2]
	if port < 1 || port > 65535 || items < 1 || items > 1048576 || sampling < 1 || sampling > 100 ||
		(args[3] != "client" && args[3] != "server") {
		return fmt.Errorf("invalid counter settings")
	}
	output := json.NewEncoder(os.Stdout)
	os.Stdout = os.Stderr
	uacp.DefaultServerACK.MaxMessageSize = maxMessageSize
	uacp.DefaultServerACK.MaxChunkCount = maxMessageSize / uacp.DefaultServerACK.ReceiveBufSize * 2
	ua.MaxVariantArrayLength = 1 << 26
	srv := server.New(server.EndPoint("127.0.0.1", port), server.EnableAuthMode(ua.UserTokenTypeAnonymous),
		server.EnableSecurity("None", ua.MessageSecurityModeNone), server.ServerName("gopcua counter server"))
	ns := server.NewNodeNameSpace(srv, applicationURI)
	if ns.ID() != 1 {
		return fmt.Errorf("counter namespace must be 1")
	}
	root, err := srv.Namespace(0)
	if err != nil {
		return err
	}
	nodes := make([]*ua.NodeID, items)
	values := make([]atomic.Uint64, items)
	for i := range nodes {
		nodes[i] = ua.NewNumericNodeID(1, uint32(i+1))
		name := fmt.Sprintf("Counter%d", i)
		node := server.NewNode(nodes[i], server.Attributes{
			ua.AttributeIDNodeClass:               server.DataValueFromValue(uint32(ua.NodeClassVariable)),
			ua.AttributeIDBrowseName:              server.DataValueFromValue(attrs.BrowseName(name)),
			ua.AttributeIDDisplayName:             server.DataValueFromValue(attrs.DisplayName(name, "en")),
			ua.AttributeIDDataType:                server.DataValueFromValue(ua.NewNumericNodeID(0, id.UInt64)),
			ua.AttributeIDValueRank:               server.DataValueFromValue(int32(-1)),
			ua.AttributeIDAccessLevel:             server.DataValueFromValue(byte(1)),
			ua.AttributeIDUserAccessLevel:         server.DataValueFromValue(byte(1)),
			ua.AttributeIDMinimumSamplingInterval: server.DataValueFromValue(float64(0)),
		}, nil, func() *ua.DataValue {
			return &ua.DataValue{EncodingMask: ua.DataValueValue | ua.DataValueSourceTimestamp,
				Value: ua.MustVariant(values[i].Load()), SourceTimestamp: time.Now()}
		})
		ns.AddNode(node)
		root.Objects().AddRef(node, id.Organizes, true)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := srv.Start(ctx); err != nil {
		return err
	}
	defer func() {
		cancel()
		srv.Close()
	}()
	ready := map[string]any{"event": "ready", "sdk": "gopcua", "runtime": runtime.Version(),
		"clock": "CLOCK_MONOTONIC", "counter_source": "application_update"}
	if build, ok := debug.ReadBuildInfo(); ok {
		for _, dependency := range build.Deps {
			if dependency.Path == "github.com/gopcua/opcua" {
				ready["version"] = dependency.Version
			}
		}
	}
	if err := output.Encode(ready); err != nil {
		return err
	}
	commands := make(chan string)
	inputErrors := make(chan error, 1)
	go func() {
		defer close(commands)
		input := bufio.NewScanner(os.Stdin)
		for input.Scan() {
			select {
			case commands <- input.Text():
			case <-ctx.Done():
				return
			}
		}
		inputErrors <- input.Err()
	}()
	results := make(chan map[string]any, 1)
	armed := false
	for {
		select {
		case line, ok := <-commands:
			if !ok {
				return <-inputErrors
			}
			if line == "quit" {
				return nil
			}
			if armed {
				return fmt.Errorf("already armed")
			}
			window, err := parseCounterWindow(line)
			if err != nil {
				return err
			}
			if err := output.Encode(window); err != nil {
				return err
			}
			armed = true
			go func() {
				results <- produceCounters(ctx, srv, nodes, values, window, int64(sampling)*1000000, args[3] == "server")
			}()
		case result := <-results:
			if err := output.Encode(result); err != nil {
				return err
			}
		}
	}
}
