// SPDX-License-Identifier: MIT
#pragma once
#include "operations.hpp"

namespace eos_transport {
inline bool Engine::send(Peer& peer, Packet packet, int socket, uint8_t channel, bool reliable) {
    if (peer.failed || peer.kicked || !peer.user || !peer.session.bound || ending) return false;
    EOS_P2P_GetPacketQueueInfoOptions get_queue{}; get_queue.ApiVersion = EOS_P2P_GETPACKETQUEUEINFO_API_LATEST;
    if (EOS_P2P_GetPacketQueueInfo(p2p, &get_queue, &queue) != Success || queue.OutgoingPacketQueueCurrentSizeBytes > 24576 || queue.OutgoingPacketQueueCurrentPacketCount > 32) { ++skipped; return false; }
    const auto bytes = encode(packet); need(bytes.size() <= EOS_P2P_MAX_PACKET_SIZE);
    EOS_P2P_SendPacketOptions options{}; options.ApiVersion = EOS_P2P_SENDPACKET_API_LATEST;
    options.LocalUserId = local; options.RemoteUserId = peer.user; options.SocketId = &sockets[socket];
    options.Channel = channel; options.DataLengthBytes = static_cast<uint32_t>(bytes.size()); options.Data = bytes.data();
    options.bAllowDelayedDelivery = EOS_FALSE; options.bDisableAutoAcceptConnection = EOS_TRUE;
    options.Reliability = reliable ? EOS_EPacketReliability::EOS_PR_ReliableOrdered : EOS_EPacketReliability::EOS_PR_UnreliableUnordered;
    const auto result = EOS_P2P_SendPacket(p2p, &options); last_sdk_result = static_cast<uint64_t>(result);
    if (result != Success) { ++skipped; return false; }
    ++sent; return true;
}
inline void Engine::networking(uint64_t now) {
    now = qpc();
    EOS_P2P_GetPacketQueueInfoOptions query{}; query.ApiVersion = EOS_P2P_GETPACKETQUEUEINFO_API_LATEST;
    need(EOS_P2P_GetPacketQueueInfo(p2p, &query, &queue) == Success);
    for (auto& peer : peers) if (peer.user && !peer.kicked && !peer.failed) {
        if (!peer.session.ready() && peer.reconnect_until && now > peer.reconnect_until) {
            reset(peer, "reconnect_timeout", true); peer.failed = true;
            emit("peer", peer_fields(peer) + ",\"user_code\":" + quote(code(peer.user)) + ",\"state\":\"failed\""); continue;
        }
        if (!peer.session.bound) continue;
        for (int socket = 0; socket < 2; ++socket) if (!peer.session.acknowledged[socket] && now >= peer.session.hello_due[socket]) {
            peer.session.hello_due[socket] = now + frequency;
            if (handshake_packets.take(1, now, frequency)) {
                EOS_P2P_AcceptConnectionOptions accept{}; accept.ApiVersion = EOS_P2P_ACCEPTCONNECTION_API_LATEST;
                accept.LocalUserId = local; accept.RemoteUserId = peer.user; accept.SocketId = &sockets[socket]; EOS_P2P_AcceptConnection(p2p, &accept);
                Packet hello{}; hello.kind = Kind::Hello; hello.sender = peer.session.local; send(peer, std::move(hello), socket, 0);
            }
        }
        if (!peer.session.ready() || peer.latest.empty() || peer.sample_id == peer.sent_sample) continue;
        const bool future = peer.sample_qpc > now && peer.sample_qpc - now > frequency / 200;
        const bool old = now >= peer.sample_qpc && now - peer.sample_qpc > frequency / 10;
        if (future || old) { peer.latest.clear(); ++skipped; continue; }
        if (queue.OutgoingPacketQueueCurrentPacketCount > 9 || queue.OutgoingPacketQueueCurrentSizeBytes > 12288) {
            if (!peer.pending_since) peer.pending_since = now;
            else if (now - peer.pending_since > frequency / 10) {
                // Conservative DATA-only shedding. SDK queue info is aggregate,
                // not a claim that any specific packet is this old. Reliable
                // application metadata uses sockets[1] and is never cleared here.
                EOS_P2P_ClearPacketQueueOptions clear{}; clear.ApiVersion = EOS_P2P_CLEARPACKETQUEUE_API_LATEST;
                clear.LocalUserId = local; clear.RemoteUserId = peer.user; clear.SocketId = &sockets[0];
                EOS_P2P_ClearPacketQueue(p2p, &clear); ++queue_clears; peer.pending_since = now;
            }
            ++skipped; continue;
        }
        peer.pending_since = 0;
        if (!data_packets.take(1, now, frequency) || !data_bytes.take(static_cast<double>(peer.latest.size() + HeaderBytes), now, frequency)) { ++skipped; continue; }
        Packet packet{}; packet.kind = Kind::Data; packet.sender = peer.session.local; packet.recipient = peer.session.remote;
        packet.sequence = peer.session.sent_data + 1; packet.payload = peer.latest;
        if (send(peer, std::move(packet), 0, 1)) { ++peer.session.sent_data; peer.sent_sample = peer.sample_id; peer.latest.clear(); }
    }
    for (unsigned at = 0; at < 64; ++at) {
        EOS_P2P_GetNextReceivedPacketSizeOptions next{}; next.ApiVersion = EOS_P2P_GETNEXTRECEIVEDPACKETSIZE_API_LATEST; next.LocalUserId = local;
        uint32_t original = 0; const auto peek = EOS_P2P_GetNextReceivedPacketSize(p2p, &next, &original);
        if (peek == Result::EOS_NotFound) break;
        if (peek != Success) { ++rejected; break; }
        std::array<uint8_t, EOS_P2P_MAX_PACKET_SIZE> bytes{}; EOS_ProductUserId sender = nullptr; EOS_P2P_SocketId socket{};
        uint8_t channel = 0; uint32_t copied = 0;
        EOS_P2P_ReceivePacketOptions options{}; options.ApiVersion = EOS_P2P_RECEIVEPACKET_API_LATEST; options.LocalUserId = local; options.MaxDataSizeBytes = static_cast<uint32_t>(bytes.size());
        if (EOS_P2P_ReceivePacket(p2p, &options, &sender, &socket, &channel, bytes.data(), &copied) != Success) { ++rejected; break; }
        const auto received_qpc = qpc();  // SDK dequeue stamp; not original network arrival/capture time.
        auto peer = find(sender); const int index = socket_index(&socket); Packet packet{};
        if (!peer || peer->kicked || peer->failed || index < 0 || !peer->session.bound || !peer->session.relayed[index] ||
                original != copied || !decode(bytes.data(), copied, index == 1, channel, packet)) { ++rejected; continue; }
        if (packet.kind == Kind::Hello) {
            if (handshake_packets.take(1, received_qpc, frequency)) {
                Packet ack{}; ack.kind = Kind::Ack; ack.sender = peer->session.local; ack.recipient = packet.sender; send(*peer, std::move(ack), index, 0);
            } else ++rejected;
        } else if (packet.kind == Kind::Ack) {
            const auto acknowledged = peer->session.acknowledge(packet, index);
            if (acknowledged == AckResult::Conflict) { reset(*peer, "session_conflict", true); continue; }
            if (acknowledged == AckResult::Rejected) { ++rejected; continue; }
            if (peer->session.ready() && !peer->published_ready) {
                peer->published_ready = true; peer->reconnect_until = 0;
                emit("peer_ready", peer_fields(*peer) + ",\"local_nonce\":" + quote(hex(peer->session.local)) +
                    ",\"remote_nonce\":" + quote(hex(peer->session.remote)) + ",\"route\":\"relay\"");
            }
        } else {
            const bool control = packet.kind == Kind::Control;
            auto& receive_budget = control ? peer->incoming_control : peer->incoming_data;
            if (!receive_budget.take(1, received_qpc, frequency)) {
                ++rejected;
                if (control) {
                    reset(*peer, "control_rate_limited", true); peer->failed = true;
                    emit("peer", peer_fields(*peer) + ",\"user_code\":" + quote(code(peer->user)) + ",\"state\":\"failed\"");
                }
                continue;
            }
            if (!peer->session.receive(packet, control)) { ++rejected; continue; }
            ++received;
            emit(control ? "control_received" : "data_received", peer_fields(*peer) + ",\"local_nonce\":" + quote(hex(peer->session.local)) +
                ",\"remote_nonce\":" + quote(hex(peer->session.remote)) + ",\"sequence\":" + integer(packet.sequence) +
                ",\"received_qpc\":" + integer(received_qpc) + ",\"payload_b64\":" + quote(base64(packet.payload)), control ? 0 : peer->ref);
        }
    }
}
inline int Engine::run() {
    initialize(); uint64_t next_tick = qpc();
    while (!ending && !fatal && !pipes.failed && !pipes.eof) {
        const auto now = qpc();
        if (WaitForSingleObject(parent, 0) != WAIT_TIMEOUT) { fail("parent_exited"); break; }
        if (now >= pipes.heartbeat_qpc && now - pipes.heartbeat_qpc > 10 * frequency) { fail("parent_lease_expired"); break; }
        if (!authenticated && now > auth_until) { fail("authentication_timeout"); break; }
        if (!pending_operation.empty() && now > operation_until) { fail("operation_timeout"); break; }
        EOS_Platform_Tick(platform_handle);
        for (unsigned at = 0; at < 8 && !ending && !fatal; ++at) {
            std::string line; if (!pipes.take(line)) break;
            Object object;
            try { object = Parser(line).parse(); command(object); }
            catch (const Invalid&) {
                ++rejected;
                const auto found = object.find("request_id"); if (found != object.end() && found->second.number && found->second.integer) reply(found->second.integer, false, "invalid_command");
            }
        }
        if (authenticated && !ending && !fatal) {
            if (!lobby.empty() && (roster_dirty || now >= next_roster)) { reconcile(); next_roster = now + frequency; }
            if (invites_dirty && pending_operation.empty()) query_invites(0);
            if (!lobby.empty()) networking(now);
            if (now >= next_stats) {
                emit("stats", ",\"sent\":" + integer(sent) + ",\"received\":" + integer(received) + ",\"rejected\":" + integer(rejected) +
                    ",\"skipped\":" + integer(skipped) + ",\"data_queue_clears\":" + integer(queue_clears) + ",\"sdk_result\":" + integer(last_sdk_result) +
                    ",\"outgoing_packets\":" + integer(queue.OutgoingPacketQueueCurrentPacketCount) + ",\"incoming_packets\":" + integer(queue.IncomingPacketQueueCurrentPacketCount));
                next_stats = now + 2 * frequency;
            }
        }
        next_tick += frequency / 60; const auto after = qpc(); if (next_tick < after) next_tick = after;
        const auto remaining_ms = (next_tick - after) * 1000 / frequency; if (remaining_ms) Sleep(static_cast<DWORD>((std::min)(remaining_ms, uint64_t{16})));
    }
    return fatal || pipes.failed ? 2 : 0;
}
inline Engine::~Engine() {
    ending = true;
    try {
        const std::string final_lobby = lobby;
        if (platform_handle) {
            if (!lobby.empty()) clear_room("helper_stopping");
            if (local && !final_lobby.empty()) {
                EOS_Lobby_LeaveLobbyOptions options{}; options.ApiVersion = EOS_LOBBY_LEAVELOBBY_API_LATEST; options.LocalUserId = local; options.LobbyId = final_lobby.c_str();
                EOS_Lobby_LeaveLobby(lobbies, &options, this, callback<EOS_Lobby_LeaveLobbyCallbackInfo, &Engine::on_left>);
                const auto until = qpc() + frequency / 2; while (qpc() < until) { EOS_Platform_Tick(platform_handle); Sleep(10); }
            }
            if (notices[0] != EOS_INVALID_NOTIFICATIONID) EOS_Lobby_RemoveNotifyLobbyMemberStatusReceived(lobbies, notices[0]);
            if (notices[1] != EOS_INVALID_NOTIFICATIONID) EOS_Lobby_RemoveNotifyLobbyInviteReceived(lobbies, notices[1]);
            if (notices[2] != EOS_INVALID_NOTIFICATIONID) EOS_P2P_RemoveNotifyPeerConnectionRequest(p2p, notices[2]);
            if (notices[3] != EOS_INVALID_NOTIFICATIONID) EOS_P2P_RemoveNotifyPeerConnectionEstablished(p2p, notices[3]);
            if (notices[4] != EOS_INVALID_NOTIFICATIONID) EOS_P2P_RemoveNotifyPeerConnectionInterrupted(p2p, notices[4]);
            if (notices[5] != EOS_INVALID_NOTIFICATIONID) EOS_P2P_RemoveNotifyPeerConnectionClosed(p2p, notices[5]);
            if (notices[6] != EOS_INVALID_NOTIFICATIONID) EOS_Connect_RemoveNotifyAuthExpiration(connect, notices[6]);
            if (notices[7] != EOS_INVALID_NOTIFICATIONID) EOS_Connect_RemoveNotifyLoginStatusChanged(connect, notices[7]);
            if (joining_details) EOS_LobbyDetails_Release(joining_details);
            EOS_Platform_Release(platform_handle);
        }
        if (initialized) EOS_Shutdown();
    } catch (...) { /* Destruction must not echo exceptions or prevent process exit. */ }
    if (parent) CloseHandle(parent);
}
}  // namespace eos_transport
