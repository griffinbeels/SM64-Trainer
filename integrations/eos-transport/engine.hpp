// SPDX-License-Identifier: MIT
// Standalone EOS identity/room/opaque transport. No Trainer/game dependencies.
#pragma once
#include "platform.hpp"
#include "state.hpp"
#include <eos_sdk.h>
#include <eos_connect.h>
#include <eos_lobby.h>
#include <eos_p2p.h>
#include <eos_logging.h>
#include <cstring>
#include <memory>

namespace eos_transport {
using Result = EOS_EResult;
constexpr Result Success = Result::EOS_Success;
struct Invite { uint64_t ref = 0; std::string token, owner; uint32_t count = 0; };
struct Peer : PeerState {
    EOS_ProductUserId user = nullptr;
};
class Engine {
    Configuration configuration;
    std::string cache_directory;
    Pipes& pipes;
    HANDLE parent = nullptr;
    EOS_HPlatform platform_handle = nullptr;
    EOS_HConnect connect = nullptr;
    EOS_HLobby lobbies = nullptr;
    EOS_HP2P p2p = nullptr;
    EOS_ProductUserId local = nullptr, owner = nullptr;
    std::array<EOS_P2P_SocketId, 2> sockets{};
    std::array<EOS_NotificationId, 8> notices{};
    std::array<Peer, 3> peers{};
    std::array<EOS_ProductUserId, 3> joined_events{};
    std::vector<Invite> invites;
    InviteQuery invite_query;
    std::string lobby, pending_operation;
    std::string leaving_lobby;
    EOS_HLobbyDetails joining_details = nullptr;
    uint64_t room_epoch = 0, next_ref = 1, next_invite = 1, invite_revision = 0;
    uint64_t request = 0, last_request = 0, operation_until = 0, auth_until = 0, frequency = qpc_frequency();
    uint64_t sent = 0, received = 0, rejected = 0, skipped = 0, queue_clears = 0;
    uint64_t last_sdk_result = 0, next_stats = 0, next_roster = 0;
    bool initialized = false, authenticated = false, ending = false, fatal = false, roster_dirty = false, invites_dirty = false;
    bool seed_roster = false;
    Budget data_packets{6, 120}, data_bytes{12288, 131072}, handshake_packets{12, 12}, control_packets{ControlPacketBurst, ControlPacketRate}, control_bytes{3072, 8192};
    EOS_P2P_PacketQueueInfo queue{};

    std::string code(EOS_ProductUserId user) const {
        if (!user) return {};
        std::array<char, EOS_PRODUCTUSERID_MAX_LENGTH + 1> bytes{}; int32_t length = static_cast<int32_t>(bytes.size());
        need(EOS_ProductUserId_ToString(user, bytes.data(), &length) == Success);
        Nonce validated{}; need(unhex(bytes.data(), validated)); return "ET1-" + std::string(bytes.data());
    }
    EOS_ProductUserId user_from_code(const std::string& value) const {
        Nonce validated{}; need(value.size() == 36 && value.compare(0, 4, "ET1-") == 0 && unhex(value.substr(4), validated));
        return EOS_ProductUserId_FromString(value.c_str() + 4);
    }
    void emit(const char* type, const std::string& fields = {}, uint64_t latest = 0) { pipes.emit(event(type, fields), latest); }
    void reply(uint64_t id, bool okay, const char* error = "none") {
        if (id) emit("reply", ",\"request_id\":" + integer(id) + ",\"ok\":" + (okay ? "true" : "false") + ",\"error\":" + quote(error));
    }
    void fail(const char* reason) noexcept {
        fatal = true;
        try { emit("fatal", ",\"code\":" + quote(reason)); } catch (...) { pipes.failed = true; }
    }
    template<class Info, void (Engine::*Method)(const Info&)>
    static void EOS_CALL callback(const Info* info) noexcept {
        auto& self = *static_cast<Engine*>(info->ClientData);
        if (self.ending) return;
        try { (self.*Method)(*info); } catch (...) { self.fail("sdk_callback_failed"); }
    }
    void begin(uint64_t id, const char* operation) {
        need(pending_operation.empty()); request = id; pending_operation = operation; operation_until = qpc() + 20 * frequency;
    }
    void finish(Result result) {
        if (!EOS_EResult_IsOperationComplete(result)) return;
        last_sdk_result = static_cast<uint64_t>(result);
        const auto id = request; request = 0; pending_operation.clear(); operation_until = 0;
        reply(id, result == Success, result == Success ? "none" : "sdk_failure");
    }
    std::string peer_fields(const Peer& peer) const {
        return ",\"room_epoch\":" + integer(room_epoch) + ",\"peer_ref\":" + integer(peer.ref) +
            ",\"member_epoch\":" + integer(peer.member_epoch) + ",\"transport_epoch\":" + integer(peer.transport_epoch);
    }
    Peer* find(EOS_ProductUserId user) { for (auto& peer : peers) if (peer.user == user && user) return &peer; return nullptr; }
    Peer* find_ref(uint64_t ref) { for (auto& peer : peers) if (peer.ref == ref && peer.user) return &peer; return nullptr; }
    Peer& current(const Object& object, bool transport = true) {
        need(number(object, "room_epoch") == room_epoch && !lobby.empty());
        auto peer = find_ref(number(object, "peer_ref")); need(peer && !peer->kicked && number(object, "member_epoch") == peer->member_epoch);
        if (transport) need(number(object, "transport_epoch") == peer->transport_epoch);
        return *peer;
    }
    int socket_index(const EOS_P2P_SocketId* socket) const {
        if (!socket) return -1;
        for (int at = 0; at < 2; ++at) if (std::strncmp(socket->SocketName, sockets[at].SocketName, EOS_P2P_SOCKETID_SOCKETNAME_SIZE) == 0) return at;
        return -1;
    }
    void close(Peer& peer) {
        for (auto& socket : sockets) {
            EOS_P2P_CloseConnectionOptions options{}; options.ApiVersion = EOS_P2P_CLOSECONNECTION_API_LATEST;
            options.LocalUserId = local; options.RemoteUserId = peer.user; options.SocketId = &socket;
            EOS_P2P_CloseConnection(p2p, &options);
        }
    }
    void reset(Peer& peer, const char* reason, bool close_connection = false) {
        const bool new_epoch = peer.reset(qpc(), frequency, close_connection);
        pipes.discard_data(peer.ref);
        if (new_epoch) {
            emit("peer_unready", peer_fields(peer) + ",\"reason\":" + quote(reason));
            emit("peer", peer_fields(peer) + ",\"user_code\":" + quote(code(peer.user)) + ",\"state\":\"awaiting_binding\"");
        }
        if (close_connection) close(peer);
    }
    void remove(Peer& peer, const char* reason) {
        const auto user = peer.user; const auto fields = peer_fields(peer);
        peer.session.invalidate(); peer.latest.clear(); peer.published_ready = false; pipes.discard_data(peer.ref);
        emit("peer_left", fields + ",\"reason\":" + quote(reason));
        Peer retired{}; retired.user = user; peer = {}; close(retired);
    }
    void clear_room(const char* reason) {
        for (auto& peer : peers) if (peer.user) remove(peer, reason);
        lobby.clear(); owner = nullptr; roster_dirty = false; seed_roster = false; joined_events = {};
        emit("room", ",\"room_epoch\":" + integer(room_epoch) + ",\"state\":\"left\",\"owner_code\":\"\",\"local_is_owner\":false,\"member_count\":0");
    }
    bool private_details(EOS_HLobbyDetails details, EOS_LobbyDetails_Info*& info) {
        EOS_LobbyDetails_CopyInfoOptions options{}; options.ApiVersion = EOS_LOBBYDETAILS_COPYINFO_API_LATEST;
        if (EOS_LobbyDetails_CopyInfo(details, &options, &info) != Success || !info) return false;
        return info->BucketId && std::strcmp(info->BucketId,"trainer-private-v1")==0 && info->bAllowInvites &&
            info->PermissionLevel == EOS_ELobbyPermissionLevel::EOS_LPL_INVITEONLY && info->MaxMembers == 4 &&
            !info->bPresenceEnabled && !info->bRTCRoomEnabled && !info->bAllowJoinById && info->bRejoinAfterKickRequiresInvite;
    }
    void reconcile() {
        if (lobby.empty()) return;
        EOS_Lobby_CopyLobbyDetailsHandleOptions copy{}; copy.ApiVersion = EOS_LOBBY_COPYLOBBYDETAILSHANDLE_API_LATEST;
        copy.LocalUserId = local; copy.LobbyId = lobby.c_str(); EOS_HLobbyDetails details = nullptr;
        if (EOS_Lobby_CopyLobbyDetailsHandle(lobbies, &copy, &details) != Success) { clear_room("membership_lost"); return; }
        EOS_LobbyDetails_Info* info = nullptr;
        if (!private_details(details, info)) { if (info) EOS_LobbyDetails_Info_Release(info); EOS_LobbyDetails_Release(details); clear_room("room_policy_changed"); return; }
        EOS_LobbyDetails_GetMemberCountOptions count_options{}; count_options.ApiVersion = EOS_LOBBYDETAILS_GETMEMBERCOUNT_API_LATEST;
        const auto count = EOS_LobbyDetails_GetMemberCount(details, &count_options);
        std::array<EOS_ProductUserId, 4> members{}; bool local_present = false;
        bool valid = count > 0 && count <= 4;
        for (uint32_t at = 0; valid && at < count; ++at) {
            EOS_LobbyDetails_GetMemberByIndexOptions member{}; member.ApiVersion = EOS_LOBBYDETAILS_GETMEMBERBYINDEX_API_LATEST; member.MemberIndex = at;
            members[at] = EOS_LobbyDetails_GetMemberByIndex(details, &member); valid = members[at] != nullptr;
            for (uint32_t before = 0; before < at; ++before) if (members[before] == members[at]) valid = false;
            local_present = local_present || members[at] == local;
        }
        const auto current_owner = info->LobbyOwnerUserId; EOS_LobbyDetails_Info_Release(info); EOS_LobbyDetails_Release(details);
        if (!valid || !local_present) { clear_room("membership_lost"); return; }
        for (auto& peer : peers) if (peer.user && std::find(members.begin(), members.end(), peer.user) == members.end()) remove(peer, "member_left");
        std::array<uint64_t, 3> announced{}; size_t announce_count = 0;
        for (auto user : members) if (user && user != local && !find(user) &&
                (seed_roster || std::find(joined_events.begin(), joined_events.end(), user) != joined_events.end())) {
            for (auto& peer : peers) if (!peer.user) {
                peer = {}; peer.user = user; peer.ref = peer.member_epoch = next_ref++; peer.reconnect_until = qpc() + 15 * frequency;
                announced[announce_count++] = peer.ref; break;
            }
        }
        for (auto& joined : joined_events) if (joined && find(joined)) joined = nullptr;
        owner = current_owner; roster_dirty = false; seed_roster = false;
        uint64_t admitted_count = 1; for (const auto& peer : peers) if (peer.user) ++admitted_count;
        emit("room", ",\"room_epoch\":" + integer(room_epoch) + ",\"state\":\"joined\",\"owner_code\":" + quote(code(owner)) +
            ",\"local_is_owner\":" + (owner == local ? "true" : "false") + ",\"member_count\":" + integer(admitted_count));
        for (size_t at = 0; at < announce_count; ++at) if (auto peer = find_ref(announced[at]))
            emit("peer", peer_fields(*peer) + ",\"user_code\":" + quote(code(peer->user)) + ",\"state\":\"awaiting_binding\"");
    }
    void on_device(const EOS_Connect_CreateDeviceIdCallbackInfo& info) {
        if (!EOS_EResult_IsOperationComplete(info.ResultCode)) return;
        if (info.ResultCode != Success && info.ResultCode != Result::EOS_DuplicateNotAllowed) { fail("device_identity_failed"); return; }
        EOS_Connect_Credentials credentials{}; credentials.ApiVersion = EOS_CONNECT_CREDENTIALS_API_LATEST; credentials.Type = EOS_EExternalCredentialType::EOS_ECT_DEVICEID_ACCESS_TOKEN;
        EOS_Connect_UserLoginInfo user{}; user.ApiVersion = EOS_CONNECT_USERLOGININFO_API_LATEST; user.DisplayName = "TrainerUser";
        EOS_Connect_LoginOptions options{}; options.ApiVersion = EOS_CONNECT_LOGIN_API_LATEST; options.Credentials = &credentials; options.UserLoginInfo = &user;
        EOS_Connect_Login(connect, &options, this, callback<EOS_Connect_LoginCallbackInfo, &Engine::on_login>);
    }
    void complete_login(Result result, EOS_ProductUserId user) {
        last_sdk_result = static_cast<uint64_t>(result);
        if (result != Success || !user) { fail("login_failed"); return; }
        local = user; authenticated = true; notifications();
        emit("ready", ",\"protocol\":1,\"max_payload\":1024,\"max_control\":512,\"qpc_frequency\":" + integer(frequency));
        emit("identity", ",\"user_code\":" + quote(code(local))); invites_dirty = true;
    }
    void on_login(const EOS_Connect_LoginCallbackInfo& info) {
        if (!EOS_EResult_IsOperationComplete(info.ResultCode)) return;
        if (info.ResultCode == Result::EOS_InvalidUser && info.ContinuanceToken) {
            EOS_Connect_CreateUserOptions options{}; options.ApiVersion = EOS_CONNECT_CREATEUSER_API_LATEST; options.ContinuanceToken = info.ContinuanceToken;
            EOS_Connect_CreateUser(connect, &options, this, callback<EOS_Connect_CreateUserCallbackInfo, &Engine::on_user>); return;
        }
        complete_login(info.ResultCode, info.LocalUserId);
    }
    void on_user(const EOS_Connect_CreateUserCallbackInfo& info) { if (EOS_EResult_IsOperationComplete(info.ResultCode)) complete_login(info.ResultCode, info.LocalUserId); }
    void on_created(const EOS_Lobby_CreateLobbyCallbackInfo& info) {
        if (!EOS_EResult_IsOperationComplete(info.ResultCode)) return;
        if (info.ResultCode == Success) { need(info.LobbyId && strnlen_s(info.LobbyId, 129) <= 128); lobby = info.LobbyId; seed_roster = true; reconcile(); }
        finish(info.ResultCode == Success && lobby.empty() ? Result::EOS_UnexpectedError : info.ResultCode);
    }
    void on_joined(const EOS_Lobby_JoinLobbyCallbackInfo& info) {
        if (!EOS_EResult_IsOperationComplete(info.ResultCode)) return;
        if (info.ResultCode == Success) { need(info.LobbyId && strnlen_s(info.LobbyId, 129) <= 128); lobby = info.LobbyId; seed_roster = true; reconcile(); }
        if (joining_details) { EOS_LobbyDetails_Release(joining_details); joining_details = nullptr; }
        finish(info.ResultCode == Success && lobby.empty() ? Result::EOS_UnexpectedError : info.ResultCode);
    }
    void on_invited(const EOS_Lobby_SendInviteCallbackInfo& info) { finish(info.ResultCode); }
    void on_rejected(const EOS_Lobby_RejectInviteCallbackInfo& info) { finish(info.ResultCode); invites_dirty = true; }
    void on_kicked(const EOS_Lobby_KickMemberCallbackInfo& info) {
        if (!EOS_EResult_IsOperationComplete(info.ResultCode)) return;
        if (info.ResultCode != Success) for (auto& peer : peers) if (peer.user)
            peer.failed_kick([&] { reset(peer, "kick_failed", true); });
        finish(info.ResultCode); roster_dirty = true;
    }
    void on_left(const EOS_Lobby_LeaveLobbyCallbackInfo& info) { finish(info.ResultCode); }
    void on_invites(const EOS_Lobby_QueryInvitesCallbackInfo& info) {
        if (!EOS_EResult_IsOperationComplete(info.ResultCode)) return;
        const auto completed = invite_query.finish();
        last_sdk_result = static_cast<uint64_t>(info.ResultCode);
        if (completed.publish && info.ResultCode == Success) {
            EOS_Lobby_GetInviteCountOptions count_options{}; count_options.ApiVersion = EOS_LOBBY_GETINVITECOUNT_API_LATEST; count_options.LocalUserId = local;
            // Matches the parent inbox bound and stays below the 4096-byte
            // private event limit even at maximum reference/name lengths.
            const auto count = (std::min)(EOS_Lobby_GetInviteCount(lobbies, &count_options), 16u);
            std::vector<Invite> next; std::string array = "[";
            for (uint32_t at = 0; at < count; ++at) {
                std::array<char, EOS_LOBBY_INVITEID_MAX_LENGTH + 1> token{}; int32_t size = static_cast<int32_t>(token.size());
                EOS_Lobby_GetInviteIdByIndexOptions index{}; index.ApiVersion = EOS_LOBBY_GETINVITEIDBYINDEX_API_LATEST; index.LocalUserId = local; index.Index = at;
                if (EOS_Lobby_GetInviteIdByIndex(lobbies, &index, token.data(), &size) != Success) continue;
                EOS_Lobby_CopyLobbyDetailsHandleByInviteIdOptions copy{}; copy.ApiVersion = EOS_LOBBY_COPYLOBBYDETAILSHANDLEBYINVITEID_API_LATEST; copy.InviteId = token.data();
                EOS_HLobbyDetails details = nullptr; if (EOS_Lobby_CopyLobbyDetailsHandleByInviteId(lobbies, &copy, &details) != Success) continue;
                EOS_LobbyDetails_Info* information = nullptr; const bool valid = private_details(details, information);
                if (valid) {
                    need(next_invite <= 0xffffffffULL); Invite invite{}; invite.ref = next_invite++; invite.token = token.data(); invite.owner = code(information->LobbyOwnerUserId);
                    invite.count = information->MaxMembers - (std::min)(information->MaxMembers, information->AvailableSlots); next.push_back(invite);
                    if (array.size() > 1) array += ',';
                    array += "{\"invite_ref\":" + integer(invite.ref) + ",\"owner_code\":" + quote(invite.owner) + ",\"member_count\":" + integer(invite.count) + ",\"max_members\":4}";
                }
                if (information) EOS_LobbyDetails_Info_Release(information); EOS_LobbyDetails_Release(details);
            }
            invites = std::move(next); array += ']'; ++invite_revision;
            emit("invitations", ",\"revision\":" + integer(invite_revision) + ",\"entries\":" + array);
        }
        reply(completed.request, info.ResultCode == Success, info.ResultCode == Success ? "none" : "sdk_failure");
    }
    // Continuation methods below are kept on this same SDK thread.
    void notifications();
    void member_status(const EOS_Lobby_LobbyMemberStatusReceivedCallbackInfo& info);
    void invitation(const EOS_Lobby_LobbyInviteReceivedCallbackInfo& info);
    void auth_expired(const EOS_Connect_AuthExpirationCallbackInfo& info);
    void login_changed(const EOS_Connect_LoginStatusChangedCallbackInfo& info);
    void connection_request(const EOS_P2P_OnIncomingConnectionRequestInfo& info);
    void established(const EOS_P2P_OnPeerConnectionEstablishedInfo& info);
    void interrupted(const EOS_P2P_OnPeerConnectionInterruptedInfo& info);
    void closed(const EOS_P2P_OnRemoteConnectionClosedInfo& info);
    void query_invites(uint64_t id);
    void start_leave(uint64_t id);
    bool send(Peer& peer, Packet packet, int socket, uint8_t channel, bool reliable = false);
    void command(const Object& object);
    void networking(uint64_t now);
public:
    Engine(const std::filesystem::path& config, const std::filesystem::path& cache, DWORD parent_pid, Pipes& io)
        : configuration(config), cache_directory(cache.empty() ? std::string{} : utf8(cache)), pipes(io) {
        notices.fill(EOS_INVALID_NOTIFICATIONID); invites.reserve(32);
        parent = OpenProcess(SYNCHRONIZE, FALSE, parent_pid); need(parent != nullptr);
        sockets[0].ApiVersion = sockets[1].ApiVersion = EOS_P2P_SOCKETID_API_LATEST;
        strcpy_s(sockets[0].SocketName, "SM64TrainerDataV1"); strcpy_s(sockets[1].SocketName, "SM64TrainerControlV1");
    }
    void initialize() {
        EOS_InitializeOptions init{}; init.ApiVersion = EOS_INITIALIZE_API_LATEST; init.ProductName = "SM64 Trainer EOS Transport"; init.ProductVersion = "0.1.0";
        need(EOS_Initialize(&init) == Success); initialized = true; need(EOS_Logging_SetLogLevel(EOS_ELogCategory::EOS_LC_ALL_CATEGORIES, EOS_ELogLevel::EOS_LOG_Off) == Success);
        EOS_Platform_Options options{}; options.ApiVersion = EOS_PLATFORM_OPTIONS_API_LATEST;
        options.ProductId = configuration.get("product_id"); options.SandboxId = configuration.get("sandbox_id"); options.DeploymentId = configuration.get("deployment_id");
        options.ClientCredentials.ClientId = configuration.get("client_id"); options.ClientCredentials.ClientSecret = configuration.get("client_secret");
        options.Flags = EOS_PF_DISABLE_OVERLAY | EOS_PF_DISABLE_SOCIAL_OVERLAY; options.TickBudgetInMilliseconds = 2;
        options.CacheDirectory = cache_directory.empty() ? nullptr : cache_directory.c_str();
        platform_handle = EOS_Platform_Create(&options); need(platform_handle != nullptr);
        connect = EOS_Platform_GetConnectInterface(platform_handle); lobbies = EOS_Platform_GetLobbyInterface(platform_handle); p2p = EOS_Platform_GetP2PInterface(platform_handle); need(connect && lobbies && p2p);
        EOS_P2P_SetRelayControlOptions relay{}; relay.ApiVersion = EOS_P2P_SETRELAYCONTROL_API_LATEST; relay.RelayControl = EOS_ERelayControl::EOS_RC_ForceRelays; need(EOS_P2P_SetRelayControl(p2p, &relay) == Success);
        EOS_P2P_GetRelayControlOptions get{}; get.ApiVersion = EOS_P2P_GETRELAYCONTROL_API_LATEST; EOS_ERelayControl value = EOS_ERelayControl::EOS_RC_AllowRelays;
        need(EOS_P2P_GetRelayControl(p2p, &get, &value) == Success && value == EOS_ERelayControl::EOS_RC_ForceRelays);
        EOS_P2P_SetPacketQueueSizeOptions sizes{}; sizes.ApiVersion = EOS_P2P_SETPACKETQUEUESIZE_API_LATEST; sizes.IncomingPacketQueueMaxSizeBytes = 65536; sizes.OutgoingPacketQueueMaxSizeBytes = 49152; need(EOS_P2P_SetPacketQueueSize(p2p, &sizes) == Success);
        auth_until = qpc() + 30 * frequency;
        EOS_Connect_CreateDeviceIdOptions device{}; device.ApiVersion = EOS_CONNECT_CREATEDEVICEID_API_LATEST; device.DeviceModel = "Windows PC";
        EOS_Connect_CreateDeviceId(connect, &device, this, callback<EOS_Connect_CreateDeviceIdCallbackInfo, &Engine::on_device>);
    }
    int run();
    ~Engine();
};
}  // namespace eos_transport
