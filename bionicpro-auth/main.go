package main

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"log"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"
	"golang.org/x/oauth2"
)

const cookieName = "sid"

type Session struct {
	AccessToken  string    `json:"access_token"`
	RefreshToken string    `json:"refresh_token"`
	Expiry       time.Time `json:"expiry"`
}

type app struct {
	oauth  *oauth2.Config
	issuer string
	appURL string
	rdb    *redis.Client
	aead   cipher.AEAD
	ttl    time.Duration
}

func env(k string) string {
	v := os.Getenv(k)
	if v == "" {
		log.Fatalf("%s is required", k)
	}
	return v
}

func main() {
	issuer := strings.TrimRight(env("KC_ISSUER_INTERNAL"), "/")
	public := strings.TrimRight(env("KC_ISSUER_PUBLIC"), "/")
	ttl, err := time.ParseDuration(env("SESSION_TTL"))
	if err != nil {
		log.Fatalf("SESSION_TTL: %v", err)
	}
	key, err := base64.StdEncoding.DecodeString(env("SESSION_ENC_KEY"))
	if err != nil || len(key) != 32 {
		log.Fatal("SESSION_ENC_KEY must be base64 of 32 bytes")
	}
	block, _ := aes.NewCipher(key)
	aead, _ := cipher.NewGCM(block)

	a := &app{
		oauth: &oauth2.Config{
			ClientID:     env("KC_CLIENT_ID"),
			ClientSecret: env("KC_CLIENT_SECRET"),
			RedirectURL:  env("REDIRECT_URL"),
			Scopes:       []string{"openid"},
			Endpoint: oauth2.Endpoint{
				AuthURL:  public + "/protocol/openid-connect/auth",
				TokenURL: issuer + "/protocol/openid-connect/token",
			},
		},
		issuer: issuer,
		appURL: env("APP_URL"),
		rdb:    redis.NewClient(&redis.Options{Addr: env("REDIS_ADDR")}),
		aead:   aead,
		ttl:    ttl,
	}
	if err := a.rdb.Ping(context.Background()).Err(); err != nil {
		log.Fatalf("redis: %v", err)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /auth/login", a.login)
	mux.HandleFunc("GET /auth/callback", a.callback)
	mux.HandleFunc("POST /auth/logout", a.logout)
	mux.Handle("GET /reports", a.withSession(a.reports))
	log.Println("bionicpro-auth listening on :8000")
	log.Fatal(http.ListenAndServe(":8000", mux))
}

func newID() string {
	b := make([]byte, 32)
	rand.Read(b)
	return base64.RawURLEncoding.EncodeToString(b)
}

func (a *app) put(ctx context.Context, key string, v any, ttl time.Duration) error {
	plain, _ := json.Marshal(v)
	nonce := make([]byte, a.aead.NonceSize())
	rand.Read(nonce)
	return a.rdb.Set(ctx, key, a.aead.Seal(nonce, nonce, plain, nil), ttl).Err()
}

func (a *app) get(ctx context.Context, key string, v any, del bool) error {
	cmd := a.rdb.Get(ctx, key)
	if del {
		cmd = a.rdb.GetDel(ctx, key)
	}
	box, err := cmd.Bytes()
	if err != nil {
		return err
	}
	n := a.aead.NonceSize()
	plain, err := a.aead.Open(nil, box[:n], box[n:], nil)
	if err != nil {
		return err
	}
	return json.Unmarshal(plain, v)
}

func (a *app) setCookie(w http.ResponseWriter, id string, maxAge int) {
	http.SetCookie(w, &http.Cookie{
		Name: cookieName, Value: id, Path: "/", MaxAge: maxAge,
		HttpOnly: true, Secure: true, SameSite: http.SameSiteLaxMode,
	})
}

func fail(w http.ResponseWriter, status int, msg string) {
	http.Error(w, `{"error":"`+msg+`"}`, status)
}

func (a *app) login(w http.ResponseWriter, r *http.Request) {
	state, verifier := newID(), oauth2.GenerateVerifier()
	if err := a.put(r.Context(), "login:"+state, verifier, 5*time.Minute); err != nil {
		fail(w, 500, "store unavailable")
		return
	}
	http.Redirect(w, r, a.oauth.AuthCodeURL(state, oauth2.S256ChallengeOption(verifier)), 302)
}

func (a *app) callback(w http.ResponseWriter, r *http.Request) {
	ctx, q := r.Context(), r.URL.Query()
	var verifier string
	if err := a.get(ctx, "login:"+q.Get("state"), &verifier, true); err != nil {
		fail(w, 400, "unknown or expired state")
		return
	}
	tok, err := a.oauth.Exchange(ctx, q.Get("code"), oauth2.VerifierOption(verifier))
	if err != nil {
		log.Printf("exchange: %v", err)
		fail(w, 502, "token exchange failed")
		return
	}
	id := newID()
	if err := a.put(ctx, "sess:"+id, toSession(tok), a.ttl); err != nil {
		fail(w, 500, "store unavailable")
		return
	}
	a.setCookie(w, id, int(a.ttl.Seconds()))
	http.Redirect(w, r, a.appURL, 302)
}

func toSession(tok *oauth2.Token) *Session {
	return &Session{AccessToken: tok.AccessToken, RefreshToken: tok.RefreshToken, Expiry: tok.Expiry}
}

func (a *app) withSession(next func(http.ResponseWriter, *http.Request, *Session)) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx := r.Context()
		c, err := r.Cookie(cookieName)
		if err != nil {
			fail(w, 401, "no session")
			return
		}
		var sess Session
		if err := a.get(ctx, "sess:"+c.Value, &sess, true); err != nil {
			a.setCookie(w, "", -1)
			fail(w, 401, "session expired")
			return
		}
		if time.Now().After(sess.Expiry) {
			src := a.oauth.TokenSource(ctx, &oauth2.Token{RefreshToken: sess.RefreshToken, Expiry: sess.Expiry})
			tok, err := src.Token()
			if err != nil {
				log.Printf("refresh: %v", err)
				a.setCookie(w, "", -1)
				fail(w, 401, "session expired")
				return
			}
			sess = *toSession(tok)
			log.Println("access_token refreshed")
		}
		id := newID()
		if err := a.put(ctx, "sess:"+id, &sess, a.ttl); err != nil {
			fail(w, 500, "store unavailable")
			return
		}
		a.setCookie(w, id, int(a.ttl.Seconds()))
		next(w, r, &sess)
	})
}

func (a *app) reports(w http.ResponseWriter, r *http.Request, s *Session) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"report": "usage report"})
}

func (a *app) logout(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var sess Session
	if c, err := r.Cookie(cookieName); err == nil && a.get(ctx, "sess:"+c.Value, &sess, true) == nil {
		form := url.Values{"client_id": {a.oauth.ClientID}, "client_secret": {a.oauth.ClientSecret}, "refresh_token": {sess.RefreshToken}}
		if resp, err := http.PostForm(a.issuer+"/protocol/openid-connect/logout", form); err == nil {
			resp.Body.Close()
		}
	}
	a.setCookie(w, "", -1)
	w.WriteHeader(204)
}
