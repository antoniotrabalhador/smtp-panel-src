package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"math/big"
	"mime"
	"mime/quotedprintable"
	"net/smtp"
	"strings"
	"time"
)

// SendResult holds per-recipient outcome.
type SendResult struct {
	To    string
	Error string
}

// jitteredDelay returns a duration with ±25% random jitter applied.
// This makes the send pattern less predictable to spam filters.
func jitteredDelay(base time.Duration) time.Duration {
	if base <= 0 {
		return 0
	}
	// Random value in [0, 50) → center at 25 → shift to [-25%, +25%]
	n, _ := rand.Int(rand.Reader, big.NewInt(50))
	jitterPct := n.Int64() - 25 // -25 to +24
	delta := time.Duration(int64(base) * jitterPct / 100)
	result := base + delta
	if result < time.Millisecond*100 {
		result = time.Millisecond * 100
	}
	return result
}

// SendBatch envia e-mails a todos os destinatários via Postfix local (127.0.0.1:25).
//
// Usa UMA conexão TCP persistente para o lote inteiro (SMTP multi-transação):
// cada mensagem começa com MAIL FROM e termina com o fechar do DATA writer,
// sem fechar a conexão TCP. O SMTP RFC permite múltiplas transações por sessão.
//
// Observação de spam: não há impacto pois a conexão é com o Postfix LOCAL.
// É o Postfix quem gerencia as conexões externas (e faz a entrega com DKIM, SPF etc.)
func isInsideWindow(startStr, endStr string) bool {
	if startStr == "" || endStr == "" {
		return true // Sem restrição
	}
	brtLoc := time.FixedZone("BRT", -3*3600)
	now := time.Now().In(brtLoc)
	currentHM := now.Format("15:04")

	if startStr <= endStr {
		return currentHM >= startStr && currentHM <= endStr
	}
	return currentHM >= startStr || currentHM <= endStr
}

func waitUntilWindowOpens(ctx context.Context, taskID int, startStr, endStr string) bool {
	logged := false
	for {
		select {
		case <-ctx.Done():
			return false
		default:
		}

		if isInsideWindow(startStr, endStr) {
			if logged {
				log.Printf("[task %d] Janela de disparo aberta (%s - %s). Retomando envios...", taskID, startStr, endStr)
			}
			return true
		}

		if !logged {
			log.Printf("[task %d] Fora da janela de disparo (%s - %s). Aguardando abertura...", taskID, startStr, endStr)
			logged = true
		}

		select {
		case <-ctx.Done():
			return false
		case <-time.After(30 * time.Second):
		}
	}
}

func SendBatch(ctx context.Context, task *Task) []SendResult {
	results := make([]SendResult, 0, len(task.Recipients))

	// Parse A/B subjects list
	var subjectList []string
	if task.Subjects != "" {
		_ = json.Unmarshal([]byte(task.Subjects), &subjectList)
	}
	if len(subjectList) == 0 {
		subjectList = []string{task.Subject}
	}

	var baseDelay time.Duration
	if task.RatePerHour > 0 {
		baseDelay = time.Hour / time.Duration(task.RatePerHour)
	}

	start := time.Now()
	total := len(task.Recipients)

	conn, err := openSMTPConn()
	if err != nil {
		log.Printf("[task %d] aviso: conexão persistente falhou (%v) — modo individual", task.ID, err)
	}
	if conn != nil {
		defer func() { _ = conn.Quit() }()
	}

	for i, to := range task.Recipients {
		select {
		case <-ctx.Done():
			log.Printf("[task %d] cancelado após %d/%d envios — reportando parcial", task.ID, i, total)
			return results
		default:
		}

		// Aguarda janela de horário se configurada
		if !waitUntilWindowOpens(ctx, task.ID, task.WindowStart, task.WindowEnd) {
			log.Printf("[task %d] cancelado durante espera da janela após %d/%d envios", task.ID, i, total)
			return results
		}

		result := SendResult{To: to}

		if conn != nil {
			err = sendOnePersistentIdx(conn, task, to, i, subjectList)
		} else {
			err = sendOneIdx(task, to, i, subjectList)
		}

		// Se a conexão SMTP caiu (idle timeout de 5min do Postfix), reconecta e RE-TENTA o envio imediatamente
		if err != nil && isConnError(err) {
			log.Printf("[task %d] conexão SMTP inativa/perdida ao enviar para %s — reconectando e re-tentando...", task.ID, to)
			if conn != nil {
				_ = conn.Close()
				conn = nil
			}
			// Tenta reconectar ou envia via conexão individual
			freshConn, connErr := openSMTPConn()
			if connErr == nil {
				conn = freshConn
				err = sendOnePersistentIdx(conn, task, to, i, subjectList)
			} else {
				err = sendOneIdx(task, to, i, subjectList)
			}
		}

		if err != nil {
			result.Error = err.Error()
		}
		results = append(results, result)

		// Log de progresso a cada 100 envios ou no final
		if (i+1)%100 == 0 || i+1 == total {
			elapsed := time.Since(start)
			realRate := 0.0
			if elapsed > 0 {
				realRate = float64(i+1) / elapsed.Hours()
			}
			log.Printf("[task %d] progresso %d/%d (%.0f/h real, %d/h alvo)",
				task.ID, i+1, total, realRate, task.RatePerHour)
		}

		if baseDelay > 0 && i+1 < total {
			// Se o delay entre e-mails for longo (>= 30s), encerra a conexão inativa
			// para não estourar o smtpd_timeout do Postfix enquanto aguarda o próximo e-mail
			if conn != nil && baseDelay >= 30*time.Second {
				_ = conn.Quit()
				conn = nil
			}

			// Usa select para que o delay também seja interrompível pelo ctx
			select {
			case <-ctx.Done():
				log.Printf("[task %d] cancelado durante espera após %d/%d envios", task.ID, i+1, total)
				return results
			case <-time.After(jitteredDelay(baseDelay)):
			}
		}
	}

	return results
}

func sendOneIdx(task *Task, to string, idx int, subjects []string) error {
	msg := buildMessage(task, to, idx, subjects)
	return sendToLocalPostfix(task.FromAddress, []string{to}, []byte(msg))
}

// Legacy wrappers for backward compat
func sendOne(task *Task, to string) error {
	return sendOneIdx(task, to, 0, []string{task.Subject})
}

// openSMTPConn abre e inicializa uma conexão SMTP com o Postfix local.
func openSMTPConn() (*smtp.Client, error) {
	c, err := smtp.Dial("127.0.0.1:25")
	if err != nil {
		return nil, err
	}
	if err := c.Hello("localhost"); err != nil {
		_ = c.Close()
		return nil, err
	}
	return c, nil
}

// sendOnePersistentIdx envia uma mensagem em uma conexão SMTP já aberta e inicializada.
// Após o DATA ser aceito, a conexão permanece aberta para a próxima transação.
// Em caso de erro no RCPT (destinatário inválido), faz RSET para limpar o estado
// da transação sem precisar reconectar.
func sendOnePersistentIdx(conn *smtp.Client, task *Task, to string, idx int, subjects []string) error {
	msg := buildMessage(task, to, idx, subjects)
	return sendViaPersistent(conn, task.FromAddress, []string{to}, []byte(msg))
}

func sendOnePersistent(conn *smtp.Client, task *Task, to string) error {
	return sendOnePersistentIdx(conn, task, to, 0, []string{task.Subject})
}

func sendViaPersistent(c *smtp.Client, from string, to []string, msg []byte) error {
	if err := c.Mail(from); err != nil {
		return err
	}
	for _, recipient := range to {
		if err := c.Rcpt(recipient); err != nil {
			_ = c.Reset()
			return err
		}
	}
	w, err := c.Data()
	if err != nil {
		return err
	}
	if _, err := w.Write([]byte(msg)); err != nil {
		_ = w.Close()
		return err
	}
	return w.Close()
}

// isConnError retorna true se o erro indica que a conexão TCP foi perdida.
// Nesses casos, reconectar é a ação correta. Erros SMTP normais (5xx) não
// são erros de conexão e não precisam de reconexão.
func isConnError(err error) bool {
	if err == nil {
		return false
	}
	msg := err.Error()
	return strings.Contains(msg, "EOF") ||
		strings.Contains(msg, "broken pipe") ||
		strings.Contains(msg, "connection reset") ||
		strings.Contains(msg, "connection refused") ||
		strings.Contains(msg, "i/o timeout") ||
		strings.Contains(msg, "use of closed network connection")
}

func sendToLocalPostfix(from string, to []string, msg []byte) error {
	client, err := smtp.Dial("127.0.0.1:25")
	if err != nil {
		return err
	}
	defer client.Close()

	if err := client.Hello("localhost"); err != nil {
		return err
	}
	if err := client.Mail(from); err != nil {
		return err
	}
	for _, recipient := range to {
		if err := client.Rcpt(recipient); err != nil {
			return err
		}
	}

	writer, err := client.Data()
	if err != nil {
		return err
	}
	if _, err := writer.Write(msg); err != nil {
		_ = writer.Close()
		return err
	}
	if err := writer.Close(); err != nil {
		return err
	}
	return client.Quit()
}

func randomMessageID(domain string) string {
	b := make([]byte, 16)
	rand.Read(b)
	return fmt.Sprintf("<%s@%s>", hex.EncodeToString(b), domain)
}

func extractDomain(email string) string {
	parts := strings.SplitN(email, "@", 2)
	if len(parts) == 2 {
		return parts[1]
	}
	return "localhost"
}

// generateProtocol gera uma string de 10 dígitos embaralhados aleatoriamente.
// Usada como variável {{protocol}} nos templates para adicionar entropia ao conteúdo
// e dificultar detecção de padrões por filtros de spam baseados em hash.
// Lê todos os bytes aleatórios em UMA única chamada ao invés de 9 alocações.
func generateProtocol() string {
	digits := []byte("0123456789")
	b := make([]byte, len(digits)) // 1 alocação para todos os bytes aleatórios
	rand.Read(b)
	for i := len(digits) - 1; i > 0; i-- {
		j := int(b[i]) % (i + 1) // Fisher-Yates com bytes pré-lidos
		digits[i], digits[j] = digits[j], digits[i]
	}
	return string(digits)
}

func replaceTags(s, to string, task *Task, protocol string) string {
	domain := extractDomain(to)

	// Fuso horário de Brasília (UTC-3)
	brtLoc := time.FixedZone("BRT", -3*3600)
	now := time.Now().In(brtLoc)
	dataStr := now.Format("02/01/2006")
	horaStr := now.Format("15:04:05")
	horaCurtaStr := now.Format("15:04")

	// Resolve tags dentro da CtaURL (ex: https://site.com/?email={{email}}&data={{data}})
	ctaResolved := task.CtaURL
	if ctaResolved != "" {
		ctaResolved = strings.ReplaceAll(ctaResolved, "{{email}}", to)
		ctaResolved = strings.ReplaceAll(ctaResolved, "{{domain}}", domain)
		ctaResolved = strings.ReplaceAll(ctaResolved, "{{protocol}}", protocol)
		ctaResolved = strings.ReplaceAll(ctaResolved, "{{subject}}", task.Subject)
		ctaResolved = strings.ReplaceAll(ctaResolved, "{{data}}", dataStr)
		ctaResolved = strings.ReplaceAll(ctaResolved, "{{date}}", dataStr)
		ctaResolved = strings.ReplaceAll(ctaResolved, "{{hora}}", horaStr)
		ctaResolved = strings.ReplaceAll(ctaResolved, "{{time}}", horaStr)
	}

	// Resolve tags dentro da UnsubscribeURL
	unsubResolved := task.UnsubscribeURL
	if unsubResolved != "" {
		unsubResolved = strings.ReplaceAll(unsubResolved, "{{email}}", to)
		unsubResolved = strings.ReplaceAll(unsubResolved, "{{domain}}", domain)
		unsubResolved = strings.ReplaceAll(unsubResolved, "{{protocol}}", protocol)
	}

	s = strings.ReplaceAll(s, "{{cta_url}}", ctaResolved)
	s = strings.ReplaceAll(s, "{{unsubscribe_url}}", unsubResolved)
	s = strings.ReplaceAll(s, "{{email}}", to)
	s = strings.ReplaceAll(s, "{{domain}}", domain)
	s = strings.ReplaceAll(s, "{{protocol}}", protocol)
	s = strings.ReplaceAll(s, "{{subject}}", task.Subject)
	s = strings.ReplaceAll(s, "{{data}}", dataStr)
	s = strings.ReplaceAll(s, "{{date}}", dataStr)
	s = strings.ReplaceAll(s, "{{hora}}", horaStr)
	s = strings.ReplaceAll(s, "{{hora_curta}}", horaCurtaStr)
	s = strings.ReplaceAll(s, "{{time}}", horaStr)

	return s
}

func encodeQuotedPrintable(s string) string {
	var buf bytes.Buffer
	writer := quotedprintable.NewWriter(&buf)
	_, _ = writer.Write([]byte(s))
	_ = writer.Close()
	return buf.String()
}

func buildMessage(task *Task, to string, recipientIdx int, subjectList []string) string {
	var sb strings.Builder

	domain := extractDomain(task.FromAddress)
	msgID := randomMessageID(domain)
	// Usa o fuso horário de Brasília (UTC-3) para os cabeçalhos de data
	brtLoc := time.FixedZone("BRT", -3*3600)
	now := time.Now().In(brtLoc)
	protocol := generateProtocol()

	// ── Core headers ──────────────────────────────────────────────────────────
	// A/B subject rotation: pick subject based on recipient index
	selectedSubject := task.Subject
	if len(subjectList) > 0 {
		selectedSubject = subjectList[recipientIdx%len(subjectList)]
	}
	subject := replaceTags(selectedSubject, to, task, protocol)
	html := replaceTags(task.HTML, to, task, protocol)
	plain := replaceTags(task.PlainText, to, task, protocol)

	// Build From header: "Sender Name" <email> or just <email>
	fromHeader := task.FromAddress
	if task.SenderName != "" {
		encodedName := mime.QEncoding.Encode("UTF-8", task.SenderName)
		fromHeader = fmt.Sprintf("%s <%s>", encodedName, task.FromAddress)
	}
	sb.WriteString(fmt.Sprintf("From: %s\r\n", fromHeader))
	sb.WriteString(fmt.Sprintf("To: %s\r\n", to))
	sb.WriteString(fmt.Sprintf("Subject: %s\r\n", mime.QEncoding.Encode("UTF-8", subject)))
	sb.WriteString(fmt.Sprintf("Date: %s\r\n", now.Format(time.RFC1123Z)))
	sb.WriteString(fmt.Sprintf("Message-ID: %s\r\n", msgID))

	// ── Routing / bounce headers ──────────────────────────────────────────────
	sb.WriteString(fmt.Sprintf("Return-Path: <%s>\r\n", task.FromAddress))

	// ── Bulk / list headers ───────────────────────────────────────────────────
	sb.WriteString("Precedence: bulk\r\n")
	sb.WriteString(fmt.Sprintf("List-ID: <newsletter.%s>\r\n", domain))

	// ── Unsubscribe (RFC 8058 One-Click) ─────────────────────────────────────
	if task.UnsubscribeURL != "" {
		unsubTo := replaceTags(task.UnsubscribeURL, to, task, protocol)
		sb.WriteString(fmt.Sprintf("List-Unsubscribe: <%s>\r\n", unsubTo))
		sb.WriteString("List-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n")
	}

	// ── Feedback-ID (Gmail spam report tracking) ──────────────────────────────
	if task.FeedbackID != "" {
		sb.WriteString(fmt.Sprintf("Feedback-ID: %s\r\n", task.FeedbackID))
	}

	// ── Anti-spam signals ─────────────────────────────────────────────────────
	sb.WriteString("X-Priority: 3\r\n")
	sb.WriteString("X-Mailer: SMTP-Fleet/1.0\r\n")

	// ── MIME multipart/alternative (HTML + plain text) ────────────────────────
	boundary := fmt.Sprintf("boundary_%s", hex.EncodeToString([]byte(msgID))[:16])
	sb.WriteString("MIME-Version: 1.0\r\n")

	if task.HTML != "" && plain != "" {
		sb.WriteString(fmt.Sprintf("Content-Type: multipart/alternative; boundary=\"%s\"\r\n", boundary))
		sb.WriteString("\r\n")

		// Plain text part
		sb.WriteString(fmt.Sprintf("--%s\r\n", boundary))
		sb.WriteString("Content-Type: text/plain; charset=UTF-8\r\n")
		sb.WriteString("Content-Transfer-Encoding: quoted-printable\r\n")
		sb.WriteString("\r\n")
		sb.WriteString(encodeQuotedPrintable(plain))
		sb.WriteString("\r\n")

		// HTML part
		sb.WriteString(fmt.Sprintf("--%s\r\n", boundary))
		sb.WriteString("Content-Type: text/html; charset=UTF-8\r\n")
		sb.WriteString("Content-Transfer-Encoding: quoted-printable\r\n")
		sb.WriteString("\r\n")
		sb.WriteString(encodeQuotedPrintable(html))
		sb.WriteString("\r\n")

		sb.WriteString(fmt.Sprintf("--%s--\r\n", boundary))
	} else if task.HTML != "" {
		sb.WriteString("Content-Type: text/html; charset=UTF-8\r\n")
		sb.WriteString("Content-Transfer-Encoding: quoted-printable\r\n")
		sb.WriteString("\r\n")
		sb.WriteString(encodeQuotedPrintable(html))
	} else {
		sb.WriteString("Content-Type: text/plain; charset=UTF-8\r\n")
		sb.WriteString("Content-Transfer-Encoding: quoted-printable\r\n")
		sb.WriteString("\r\n")
		sb.WriteString(encodeQuotedPrintable(replaceTags(task.Body, to, task, protocol)))
	}

	return sb.String()
}
