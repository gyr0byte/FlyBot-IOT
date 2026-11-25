#include "streams.h"

#include <cstring>

namespace flybot {

StreamKey StreamKey::of(Topic t, const char* src) {
    StreamKey k{};
    k.topic = t;
    std::strncpy(k.src, src ? src : "", sizeof(k.src) - 1);
    k.src[sizeof(k.src) - 1] = '\0';
    return k;
}

bool StreamKey::operator==(const StreamKey& o) const {
    return topic == o.topic && std::strcmp(src, o.src) == 0;
}

StreamTracker::Entry* StreamTracker::find(const StreamKey& key) {
    for (auto& e : entries_)
        if (e.used && e.key == key) return &e;
    return nullptr;
}

StreamTracker::Entry* StreamTracker::slot_for_new() {
    Entry* oldest = nullptr;
    for (auto& e : entries_) {
        if (!e.used) return &e;
        if (!oldest || e.last_seen < oldest->last_seen) oldest = &e;
    }
    ++evicted;
    return oldest;
}

void StreamTracker::set(Entry& e, const StreamKey& key, uint32_t seq, int64_t up, int64_t now,
                        Opt<uint32_t> bid) {
    e.used = true;
    e.key = key;
    e.seq = seq;
    e.up = up;
    e.last_seen = now;
    e.has_bid = bid.has_value();
    e.bid = bid.value_or(0);
}

bool StreamTracker::accept(const StreamKey& key, uint32_t seq, int64_t up, int64_t now,
                           Opt<uint32_t> bid) {
    last_restarted = false;
    for (auto& e : entries_)  // rule 13: forget idle senders
        if (e.used && now - e.last_seen > idle_forget_ms_) e.used = false;
    Entry* prev = find(key);
    if (!prev) {  // rule 13: new sender
        set(*slot_for_new(), key, seq, up, now, bid);
        return true;
    }
    bool restarted;
    if (bid.has_value() || prev->has_bid)
        restarted = !(bid.has_value() && prev->has_bid && *bid == prev->bid);
    else
        restarted = up < prev->up;
    if (restarted) {  // rule 4: sender restarted
        ++restarts;
        last_restarted = true;
        set(*prev, key, seq, up, now, bid);
        return true;
    }
    uint32_t diff = seq - prev->seq;  // modulo 2^32 (rule 8: wrap-safe)
    if (diff == 0 || diff >= 0x80000000u) {  // rule 5 duplicate / rule 7 late
        ++dropped;
        return false;
    }
    lost += diff - 1u;  // rules 6 and 8
    set(*prev, key, seq, up, now, bid);
    return true;
}

void StreamTracker::remember(const StreamKey& key, uint32_t seq, int64_t up, int64_t now,
                             Opt<uint32_t> bid) {
    Entry* e = find(key);
    set(e ? *e : *slot_for_new(), key, seq, up, now, bid);
}

void StreamTracker::reset(const StreamKey& key) {
    if (Entry* e = find(key)) e->used = false;
}

int StreamTracker::size() const {
    int n = 0;
    for (const auto& e : entries_) n += e.used ? 1 : 0;
    return n;
}

}  // namespace flybot
