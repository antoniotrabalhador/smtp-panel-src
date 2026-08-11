package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

type Task struct {
	ID             int      `json:"id"`
	Subject        string   `json:"subject"`
	Subjects       string   `json:"subjects"`    // JSON array for A/B testing
	SenderName     string   `json:"sender_name"` // Display name for From header
	Body           string   `json:"body"`
	HTML           string   `json:"html"`
	PlainText      string   `json:"plain_text"`
	FromAddress    string   `json:"from_address"`
	Recipients     []string `json:"recipients"`
	RatePerHour    int      `json:"rate_per_hour"`
	UnsubscribeURL string   `json:"unsubscribe_url"`
	FeedbackID     string   `json:"feedback_id"`
	CtaURL         string   `json:"cta_url"`
	WindowStart    string   `json:"window_start"`
	WindowEnd      string   `json:"window_end"`
}

type TaskReport struct {
	SentCount  int    `json:"sent_count"`
	ErrorCount int    `json:"error_count"`
	Log        string `json:"log"`
}

type Heartbeat struct {
	Status       string `json:"status"`
	Version      string `json:"version"`
	ActiveTaskID int    `json:"active_task_id,omitempty"`
}

type PanelClient struct {
	baseURL    string
	nodeID     int
	token      string
	httpClient *http.Client
}

func NewPanelClient(baseURL string, nodeID int, token string) *PanelClient {
	return &PanelClient{
		baseURL: strings.TrimRight(baseURL, "/"),
		nodeID:  nodeID,
		token:   token,
		httpClient: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

func (c *PanelClient) do(method, path string, body interface{}) (*http.Response, error) {
	var bodyReader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		bodyReader = bytes.NewReader(data)
	}

	req, err := http.NewRequest(method, c.baseURL+path, bodyReader)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Agent-Token", c.token)

	return c.httpClient.Do(req)
}

func (c *PanelClient) PollTask() (*Task, error) {
	resp, err := c.do("GET", fmt.Sprintf("/api/agent/tasks?node_id=%d", c.nodeID), nil)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode == 204 {
		return nil, nil // no task available
	}
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("poll failed: HTTP %d", resp.StatusCode)
	}

	var task Task
	if err := json.NewDecoder(resp.Body).Decode(&task); err != nil {
		return nil, err
	}
	return &task, nil
}

func (c *PanelClient) ReportTask(taskID int, report TaskReport) error {
	resp, err := c.do("POST", fmt.Sprintf("/api/agent/tasks/%d/report", taskID), report)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("report failed: HTTP %d", resp.StatusCode)
	}
	return nil
}

type HeartbeatResponse struct {
	Ok          bool `json:"ok"`
	AbortTaskID int  `json:"abort_task_id,omitempty"`
}

func (c *PanelClient) Heartbeat(activeTaskID int) (*HeartbeatResponse, error) {
	hb := Heartbeat{Status: "online", Version: "1.0.0", ActiveTaskID: activeTaskID}
	resp, err := c.do("POST", fmt.Sprintf("/api/agent/heartbeat?node_id=%d", c.nodeID), hb)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("heartbeat failed: HTTP %d", resp.StatusCode)
	}
	
	var hbResp HeartbeatResponse
	if err := json.NewDecoder(resp.Body).Decode(&hbResp); err != nil {
		return nil, err
	}
	
	return &hbResp, nil
}
