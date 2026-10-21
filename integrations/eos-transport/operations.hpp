// SPDX-License-Identifier: MIT
#pragma once
#include "engine.hpp"

namespace eos_transport {
inline void fields(const Object& object, std::initializer_list<const char*> expected) {
    need(object.size() == expected.size());
    for (auto name : expected) need(object.find(name) != object.end());
}
inline void Engine::member_status(const EOS_Lobby_LobbyMemberStatusReceivedCallbackInfo& info) {
    if (lobby.empty() || !info.LobbyId || lobby != info.LobbyId) return;
    using Status = EOS_ELobbyMemberStatus;
    if (info.CurrentStatus == Status::EOS_LMS_JOINED || info.CurrentStatus == Status::EOS_LMS_PROMOTED) {
        if (info.CurrentStatus == Status::EOS_LMS_JOINED && info.TargetUserId != local && !find(info.TargetUserId)) {
            bool stored = false;
            for (auto& joined : joined_events) if (joined == info.TargetUserId) stored = true;
            if (!stored) for (auto& joined : joined_events) if (!joined) { joined = info.TargetUserId; stored = true; break; }
            if (!stored) { fail("roster_event_overflow"); return; }
        }
        roster_dirty = true; return;
    }
    if (info.CurrentStatus != Status::EOS_LMS_LEFT && info.CurrentStatus != Status::EOS_LMS_DISCONNECTED &&
            info.CurrentStatus != Status::EOS_LMS_KICKED && info.CurrentStatus != Status::EOS_LMS_CLOSED) return;
    if (info.TargetUserId == local) { clear_room("local_membership_ended"); return; }
    for (auto& joined : joined_events) if (joined == info.TargetUserId) joined = nullptr;
    if (auto peer = find(info.TargetUserId)) remove(*peer, "member_removed");
    roster_dirty = true;
}
inline void Engine::invitation(const EOS_Lobby_LobbyInviteReceivedCallbackInfo& info) {
    if (info.LocalUserId == local) invites_dirty = true;
}
inline void Engine::auth_expired(const EOS_Connect_AuthExpirationCallbackInfo& info) {
    if (info.LocalUserId == local) fail("authentication_expiring");
}
inline void Engine::login_changed(const EOS_Connect_LoginStatusChangedCallbackInfo& info) {
    if (info.LocalUserId == local && info.CurrentStatus != EOS_ELoginStatus::EOS_LS_LoggedIn) fail("authentication_lost");
}
inline void Engine::connection_request(const EOS_P2P_OnIncomingConnectionRequestInfo& info) {
    const int index = socket_index(info.SocketId); if (index < 0 || info.LocalUserId != local) return;
    auto peer = find(info.RemoteUserId);
    if (!peer || peer->kicked || peer->failed || !peer->session.bound || lobby.empty()) {
        ++rejected;
        EOS_P2P_CloseConnectionOptions options{}; options.ApiVersion = EOS_P2P_CLOSECONNECTION_API_LATEST;
        options.LocalUserId = local; options.RemoteUserId = info.RemoteUserId; options.SocketId = &sockets[index]; EOS_P2P_CloseConnection(p2p, &options); return;
    }
    EOS_P2P_AcceptConnectionOptions options{}; options.ApiVersion = EOS_P2P_ACCEPTCONNECTION_API_LATEST;
    options.LocalUserId = local; options.RemoteUserId = peer->user; options.SocketId = &sockets[index]; EOS_P2P_AcceptConnection(p2p, &options);
}
inline void Engine::established(const EOS_P2P_OnPeerConnectionEstablishedInfo& info) {
    const int index = socket_index(info.SocketId); if (index < 0 || info.LocalUserId != local) return;
    auto peer = find(info.RemoteUserId); if (!peer || peer->kicked || peer->failed || !peer->session.bound) { ++rejected; return; }
    if (info.NetworkType != EOS_ENetworkConnectionType::EOS_NCT_RelayedConnection) { reset(*peer, "non_relay_route", true); peer->failed = true; return; }
    peer->session.relayed[index] = true;
}
inline void Engine::interrupted(const EOS_P2P_OnPeerConnectionInterruptedInfo& info) {
    if (socket_index(info.SocketId) < 0 || info.LocalUserId != local) return;
    if (auto peer = find(info.RemoteUserId)) reset(*peer, "transport_interrupted");
}
inline void Engine::closed(const EOS_P2P_OnRemoteConnectionClosedInfo& info) {
    if (socket_index(info.SocketId) < 0 || info.LocalUserId != local) return;
    if (auto peer = find(info.RemoteUserId)) reset(*peer, "transport_closed");
}
inline void Engine::notifications() {
    EOS_Lobby_AddNotifyLobbyMemberStatusReceivedOptions members{}; members.ApiVersion = EOS_LOBBY_ADDNOTIFYLOBBYMEMBERSTATUSRECEIVED_API_LATEST;
    notices[0] = EOS_Lobby_AddNotifyLobbyMemberStatusReceived(lobbies, &members, this, callback<EOS_Lobby_LobbyMemberStatusReceivedCallbackInfo, &Engine::member_status>);
    EOS_Lobby_AddNotifyLobbyInviteReceivedOptions invited{}; invited.ApiVersion = EOS_LOBBY_ADDNOTIFYLOBBYINVITERECEIVED_API_LATEST;
    notices[1] = EOS_Lobby_AddNotifyLobbyInviteReceived(lobbies, &invited, this, callback<EOS_Lobby_LobbyInviteReceivedCallbackInfo, &Engine::invitation>);
    EOS_P2P_AddNotifyPeerConnectionRequestOptions incoming{}; incoming.ApiVersion = EOS_P2P_ADDNOTIFYPEERCONNECTIONREQUEST_API_LATEST; incoming.LocalUserId = local;
    notices[2] = EOS_P2P_AddNotifyPeerConnectionRequest(p2p, &incoming, this, callback<EOS_P2P_OnIncomingConnectionRequestInfo, &Engine::connection_request>);
    EOS_P2P_AddNotifyPeerConnectionEstablishedOptions route{}; route.ApiVersion = EOS_P2P_ADDNOTIFYPEERCONNECTIONESTABLISHED_API_LATEST; route.LocalUserId = local;
    notices[3] = EOS_P2P_AddNotifyPeerConnectionEstablished(p2p, &route, this, callback<EOS_P2P_OnPeerConnectionEstablishedInfo, &Engine::established>);
    EOS_P2P_AddNotifyPeerConnectionInterruptedOptions interruption{}; interruption.ApiVersion = EOS_P2P_ADDNOTIFYPEERCONNECTIONINTERRUPTED_API_LATEST; interruption.LocalUserId = local;
    notices[4] = EOS_P2P_AddNotifyPeerConnectionInterrupted(p2p, &interruption, this, callback<EOS_P2P_OnPeerConnectionInterruptedInfo, &Engine::interrupted>);
    EOS_P2P_AddNotifyPeerConnectionClosedOptions closure{}; closure.ApiVersion = EOS_P2P_ADDNOTIFYPEERCONNECTIONCLOSED_API_LATEST; closure.LocalUserId = local;
    notices[5] = EOS_P2P_AddNotifyPeerConnectionClosed(p2p, &closure, this, callback<EOS_P2P_OnRemoteConnectionClosedInfo, &Engine::closed>);
    EOS_Connect_AddNotifyAuthExpirationOptions expiration{}; expiration.ApiVersion = EOS_CONNECT_ADDNOTIFYAUTHEXPIRATION_API_LATEST;
    notices[6] = EOS_Connect_AddNotifyAuthExpiration(connect, &expiration, this, callback<EOS_Connect_AuthExpirationCallbackInfo, &Engine::auth_expired>);
    EOS_Connect_AddNotifyLoginStatusChangedOptions login{}; login.ApiVersion = EOS_CONNECT_ADDNOTIFYLOGINSTATUSCHANGED_API_LATEST;
    notices[7] = EOS_Connect_AddNotifyLoginStatusChanged(connect, &login, this, callback<EOS_Connect_LoginStatusChangedCallbackInfo, &Engine::login_changed>);
    for (auto notification : notices) need(notification != EOS_INVALID_NOTIFICATIONID);
}
inline void Engine::query_invites(uint64_t id) {
    begin(id, "refresh_invites");
    EOS_Lobby_QueryInvitesOptions options{}; options.ApiVersion = EOS_LOBBY_QUERYINVITES_API_LATEST; options.LocalUserId = local;
    EOS_Lobby_QueryInvites(lobbies, &options, this, callback<EOS_Lobby_QueryInvitesCallbackInfo, &Engine::on_invites>);
}
inline void Engine::start_leave(uint64_t id) {
    leaving_lobby = lobby; clear_room("deliberate_leave");
    if (leaving_lobby.empty()) { reply(id, true); return; }
    begin(id, "leave");
    EOS_Lobby_LeaveLobbyOptions options{}; options.ApiVersion = EOS_LOBBY_LEAVELOBBY_API_LATEST; options.LocalUserId = local; options.LobbyId = leaving_lobby.c_str();
    EOS_Lobby_LeaveLobby(lobbies, &options, this, callback<EOS_Lobby_LeaveLobbyCallbackInfo, &Engine::on_left>);
}
inline void Engine::command(const Object& object) {
    const auto& operation = text(object, "op"); need(number(object, "v") == 1);
    const bool latest = operation == "send_latest";
    const uint64_t id = latest ? 0 : number(object, "request_id");
    if (!latest) { need(id && id > last_request); last_request = id; }
    if (operation == "shutdown") {
        fields(object, {"v", "op", "request_id"}); reply(id, true); ending = true; return;
    }
    if (!authenticated) { reply(id, false, "not_ready"); return; }
    if (operation == "identity") {
        fields(object, {"v", "op", "request_id"}); emit("identity", ",\"user_code\":" + quote(code(local))); reply(id, true); return;
    }
    if (operation == "send_latest") {
        fields(object, {"v", "op", "room_epoch", "peer_ref", "member_epoch", "transport_epoch", "sample_id", "sample_qpc", "payload_b64"});
        auto& peer = current(object); if (!peer.session.ready() || peer.failed) { ++skipped; return; }
        const auto sample = number(object, "sample_id"), stamp = number(object, "sample_qpc"); std::vector<uint8_t> payload;
        need(sample && sample > peer.sample_id && stamp && unbase64(text(object, "payload_b64"), PayloadLimit, payload));
        peer.sample_id = sample; peer.sample_qpc = stamp; peer.latest = std::move(payload); return;
    }
    if (operation == "bind_peer") {
        fields(object, {"v", "op", "request_id", "room_epoch", "peer_ref", "member_epoch", "transport_epoch", "local_nonce"});
        auto& peer = current(object); Nonce nonce{};
        if (peer.failed || !unhex(text(object, "local_nonce"), nonce) || !peer.session.bind(nonce)) { reply(id, false, "invalid_nonce"); return; }
        peer.reconnect_until = qpc() + 15 * frequency;
        for (auto& socket : sockets) {
            EOS_P2P_AcceptConnectionOptions options{}; options.ApiVersion = EOS_P2P_ACCEPTCONNECTION_API_LATEST;
            options.LocalUserId = local; options.RemoteUserId = peer.user; options.SocketId = &socket; EOS_P2P_AcceptConnection(p2p, &options);
        }
        reply(id, true); return;
    }
    if (operation == "reset_peer") {
        fields(object, {"v", "op", "request_id", "room_epoch", "peer_ref", "member_epoch", "transport_epoch"});
        auto& peer = current(object); peer.failed = false; reset(peer, "explicit_reset", true); reply(id, true); return;
    }
    if (operation == "send_control") {
        const bool has_room = object.find("room_epoch") != object.end();
        if (has_room) fields(object, {"v", "op", "request_id", "room_epoch", "peer_ref", "member_epoch", "transport_epoch", "payload_b64"});
        else fields(object, {"v", "op", "request_id", "peer_ref", "member_epoch", "transport_epoch", "payload_b64"});
        Object addressed = object; if (!has_room) addressed.emplace("room_epoch", Value{true, room_epoch, {}});
        auto& peer = current(addressed); if (!peer.session.ready()) { reply(id, false, "transport_unready"); return; }
        Packet packet{}; packet.kind = Kind::Control; packet.sender = peer.session.local; packet.recipient = peer.session.remote; packet.sequence = peer.session.sent_control + 1;
        need(unbase64(text(object, "payload_b64"), ControlLimit, packet.payload));
        const auto now = qpc(); if (!control_packets.take(1, now, frequency) || !control_bytes.take(static_cast<double>(packet.payload.size()), now, frequency)) { reply(id, false, "rate_limited"); return; }
        const bool okay = send(peer, std::move(packet), 1, 2, true); if (okay) ++peer.session.sent_control; reply(id, okay, okay ? "none" : "queue_full"); return;
    }
    if (!pending_operation.empty()) { reply(id, false, "busy"); return; }
    if (operation == "create_room") {
        fields(object, {"v", "op", "request_id"}); if (!lobby.empty()) { reply(id, false, "already_in_room"); return; }
        ++room_epoch; begin(id, "create_room");
        EOS_Lobby_CreateLobbyOptions options{}; options.ApiVersion = EOS_LOBBY_CREATELOBBY_API_LATEST; options.LocalUserId = local; options.MaxLobbyMembers = 4;
        options.PermissionLevel = EOS_ELobbyPermissionLevel::EOS_LPL_INVITEONLY; options.bAllowInvites = EOS_TRUE; options.BucketId = "trainer-private-v1";
        options.bDisableHostMigration = EOS_FALSE; options.bRejoinAfterKickRequiresInvite = EOS_TRUE;
        EOS_Lobby_CreateLobby(lobbies, &options, this, callback<EOS_Lobby_CreateLobbyCallbackInfo, &Engine::on_created>); return;
    }
    if (operation == "refresh_invites") { fields(object, {"v", "op", "request_id"}); query_invites(id); return; }
    if (operation == "accept_invite" || operation == "decline_invite") {
        fields(object, {"v", "op", "request_id", "invite_ref"}); const auto ref = number(object, "invite_ref");
        auto found = std::find_if(invites.begin(), invites.end(), [&](const Invite& invite) { return invite.ref == ref; });
        if (found == invites.end()) { reply(id, false, "expired_invite"); return; }
        if (operation == "decline_invite") {
            begin(id, "decline_invite"); EOS_Lobby_RejectInviteOptions options{}; options.ApiVersion = EOS_LOBBY_REJECTINVITE_API_LATEST; options.LocalUserId = local; options.InviteId = found->token.c_str();
            EOS_Lobby_RejectInvite(lobbies, &options, this, callback<EOS_Lobby_RejectInviteCallbackInfo, &Engine::on_rejected>); return;
        }
        if (!lobby.empty()) { reply(id, false, "already_in_room"); return; }
        EOS_Lobby_CopyLobbyDetailsHandleByInviteIdOptions copy{}; copy.ApiVersion = EOS_LOBBY_COPYLOBBYDETAILSHANDLEBYINVITEID_API_LATEST; copy.InviteId = found->token.c_str();
        if (EOS_Lobby_CopyLobbyDetailsHandleByInviteId(lobbies, &copy, &joining_details) != Success) { reply(id, false, "expired_invite"); return; }
        EOS_LobbyDetails_Info* info = nullptr; const bool valid = private_details(joining_details, info) && found->owner == code(info->LobbyOwnerUserId);
        if (info) EOS_LobbyDetails_Info_Release(info);
        if (!valid) { EOS_LobbyDetails_Release(joining_details); joining_details = nullptr; reply(id, false, "room_policy_changed"); return; }
        ++room_epoch; begin(id, "accept_invite"); EOS_Lobby_JoinLobbyOptions options{}; options.ApiVersion = EOS_LOBBY_JOINLOBBY_API_LATEST; options.LocalUserId = local; options.LobbyDetailsHandle = joining_details;
        EOS_Lobby_JoinLobby(lobbies, &options, this, callback<EOS_Lobby_JoinLobbyCallbackInfo, &Engine::on_joined>); return;
    }
    if (operation == "leave") {
        fields(object, {"v", "op", "request_id", "room_epoch"}); if (number(object, "room_epoch") != room_epoch) { reply(id, false, "stale_reference"); return; }
        start_leave(id); return;
    }
    if (operation == "invite") {
        fields(object, {"v", "op", "request_id", "room_epoch", "user_code"});
        if (lobby.empty() || number(object, "room_epoch") != room_epoch) { reply(id, false, "not_in_room"); return; }
        const auto target = user_from_code(text(object, "user_code")); need(target && target != local);
        begin(id, "invite"); EOS_Lobby_SendInviteOptions options{}; options.ApiVersion = EOS_LOBBY_SENDINVITE_API_LATEST; options.LobbyId = lobby.c_str(); options.LocalUserId = local; options.TargetUserId = target;
        EOS_Lobby_SendInvite(lobbies, &options, this, callback<EOS_Lobby_SendInviteCallbackInfo, &Engine::on_invited>); return;
    }
    if (operation == "kick") {
        fields(object, {"v", "op", "request_id", "room_epoch", "peer_ref", "member_epoch"});
        auto& peer = current(object, false); if (owner != local) { reply(id, false, "not_owner"); return; }
        peer.kicked = true; reset(peer, "kick_requested", true);
        begin(id, "kick"); EOS_Lobby_KickMemberOptions options{}; options.ApiVersion = EOS_LOBBY_KICKMEMBER_API_LATEST; options.LocalUserId = local; options.LobbyId = lobby.c_str(); options.TargetUserId = peer.user;
        EOS_Lobby_KickMember(lobbies, &options, this, callback<EOS_Lobby_KickMemberCallbackInfo, &Engine::on_kicked>); return;
    }
    reply(id, false, "unknown_command");
}
}  // namespace eos_transport
