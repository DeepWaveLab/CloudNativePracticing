// Command relay is a minimal Agones dedicated game server that relays player
// position packets between every client connected to the same GameServer.
package main

import (
	"log"
	"net"
	"strings"
	"sync"
	"time"

	coresdk "agones.dev/agones/pkg/sdk"
	"agones.dev/agones/pkg/util/signals"
	sdk "agones.dev/agones/sdks/go"
)

const (
	listenAddr  = ":7654"
	idleTimeout = 60 * time.Second
)

type peer struct {
	name     string
	addr     net.Addr
	lastSeen time.Time
}

type relay struct {
	mu        sync.Mutex
	conn      net.PacketConn
	peers     map[string]*peer
	allocated bool
	lastPkt   time.Time
	forwarded map[string]int
}

func (r *relay) handle(addr net.Addr, msg string) {
	fields := strings.Fields(msg)
	if len(fields) < 2 {
		return
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	key := addr.String()
	r.lastPkt = time.Now()

	switch fields[0] {
	case "JOIN":
		r.peers[key] = &peer{name: fields[1], addr: addr, lastSeen: time.Now()}
		log.Printf("join player=%s from=%s peers=%d", fields[1], key, len(r.peers))
		r.send(addr, "WELCOME "+fields[1])
	case "POS":
		p, ok := r.peers[key]
		if !ok {
			return
		}
		p.lastSeen = time.Now()
		for otherKey, other := range r.peers {
			if otherKey == key {
				continue
			}
			r.send(other.addr, msg)
			r.forwarded[p.name+"->"+other.name]++
			if n := r.forwarded[p.name+"->"+other.name]; n == 1 || n%50 == 0 {
				log.Printf("relay %s->%s count=%d last=%q", p.name, other.name, n, msg)
			}
		}
	case "BYE":
		if p, ok := r.peers[key]; ok {
			delete(r.peers, key)
			log.Printf("bye player=%s peers=%d", p.name, len(r.peers))
		}
	}
}

func (r *relay) send(addr net.Addr, msg string) {
	if _, err := r.conn.WriteTo([]byte(msg), addr); err != nil {
		log.Printf("write to %s: %v", addr, err)
	}
}

// shouldShutdown reports whether an allocated session has ended: every player
// said BYE, or no packet arrived within idleTimeout.
func (r *relay) shouldShutdown() bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	if !r.allocated || r.lastPkt.IsZero() {
		return false
	}
	return len(r.peers) == 0 || time.Since(r.lastPkt) > idleTimeout
}

func main() {
	ctx, cancel := signals.NewSigKillContext()
	defer cancel()

	s, err := sdk.NewSDK()
	if err != nil {
		log.Fatalf("connect sdk: %v", err)
	}
	conn, err := net.ListenPacket("udp", listenAddr)
	if err != nil {
		log.Fatalf("listen %s: %v", listenAddr, err)
	}
	defer conn.Close()

	r := &relay{conn: conn, peers: map[string]*peer{}, forwarded: map[string]int{}}

	if err := s.WatchGameServer(func(gs *coresdk.GameServer) {
		r.mu.Lock()
		defer r.mu.Unlock()
		if gs.Status.State == "Allocated" && !r.allocated {
			r.allocated = true
			log.Printf("state Allocated tokens=%s", gs.ObjectMeta.Annotations["quilkin.dev/tokens"])
		}
	}); err != nil {
		log.Fatalf("watch gameserver: %v", err)
	}

	go func() {
		tick := time.NewTicker(2 * time.Second)
		defer tick.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-tick.C:
				if err := s.Health(); err != nil {
					log.Printf("health: %v", err)
				}
				if r.shouldShutdown() {
					log.Printf("session ended, calling SDK Shutdown")
					if err := s.Shutdown(); err != nil {
						log.Printf("shutdown: %v", err)
					}
					return
				}
			}
		}
	}()

	go func() {
		buf := make([]byte, 1500)
		for {
			n, addr, err := conn.ReadFrom(buf)
			if err != nil {
				log.Printf("read: %v", err)
				return
			}
			r.handle(addr, strings.TrimSpace(string(buf[:n])))
		}
	}()

	if err := s.Ready(); err != nil {
		log.Fatalf("sdk ready: %v", err)
	}
	log.Printf("relay listening on %s, marked Ready", listenAddr)
	<-ctx.Done()
}
